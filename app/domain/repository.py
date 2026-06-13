"""Persistence and atomic repository helpers.

Provides SQLAlchemy 2.x engine/session management driven by
:data:`Settings.database_url`, an :func:`init_db` helper that creates all tables
from :data:`Base.metadata`, a :func:`session_scope` transactional context
manager, and the small set of *atomic* operations relied on by later services:

* :func:`claim_listing` — compare-and-set listing status so concurrent buyers
  yield a single winner (R6.5).
* :func:`issue_refund_once` — unique-constraint-guarded creation of the single
  successful refund per return request (R10.1, idempotent).
* :func:`credit_green_points_once` — unique-constraint-guarded points credit per
  ``(returnRequestId, type)`` (R8.6, idempotent).
* :func:`redeem_points` — all-or-nothing redemption that deducts points and
  credits Amazon Pay in one transaction (R9.2).

These helpers are well-typed and self-contained so the property/unit tests in
tasks 8/9/10 can exercise them directly.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.domain.models import (
    AmazonPayBalance,
    Base,
    Disposition,
    GreenPointsBalance,
    GreenPointsLedger,
    GreenPointsType,
    ListingStatus,
    MarketplaceListing,
    PaymentMethod,
    Refund,
    RefundStatus,
    RedemptionRecord,
)
from app.domain.money import utc_now

# ---------------------------------------------------------------------------
# Engine / session management
# ---------------------------------------------------------------------------

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _make_engine(database_url: str) -> Engine:
    """Create an :class:`Engine` for ``database_url`` with SQLite-safe options."""

    connect_args: dict = {}
    engine_kwargs: dict = {"future": True}
    if database_url.startswith("sqlite"):
        # Allow cross-thread use (FastAPI worker threads / test clients).
        connect_args["check_same_thread"] = False
        # Keep an in-memory database alive across connections within a process.
        if ":memory:" in database_url or database_url == "sqlite://":
            from sqlalchemy.pool import StaticPool

            engine_kwargs["poolclass"] = StaticPool
    return sa.create_engine(database_url, connect_args=connect_args, **engine_kwargs)


def get_engine() -> Engine:
    """Return the process-wide engine, creating it lazily from settings."""

    global _engine, _SessionFactory
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
        _SessionFactory = sessionmaker(
            bind=_engine, future=True, expire_on_commit=False
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, initializing the engine first."""

    if _SessionFactory is None:
        get_engine()
    assert _SessionFactory is not None  # for type checkers
    return _SessionFactory


def init_db(engine: Engine | None = None) -> Engine:
    """Create all tables from :data:`Base.metadata`.

    Args:
        engine: Optional engine to target. When omitted, the process-wide engine
            (derived from settings) is used.

    Returns:
        The engine the tables were created on.
    """

    target = engine or get_engine()
    Base.metadata.create_all(target)
    return target


# Alias matching the task description wording.
create_all = init_db


def drop_all(engine: Engine | None = None) -> None:
    """Drop all tables (test/teardown convenience)."""

    Base.metadata.drop_all(engine or get_engine())


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Provide a transactional session scope.

    Commits on success, rolls back on exception, and always closes the session.
    """

    factory = factory or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _new_id(prefix: str) -> str:
    """Return a short unique id with the given prefix."""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Atomic helper result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of a compare-and-set listing claim."""

    claimed: bool
    listingId: str
    buyerId: str | None


@dataclass(frozen=True)
class RefundResult:
    """Outcome of an idempotent refund issue."""

    created: bool
    refundId: str


@dataclass(frozen=True)
class CreditResult:
    """Outcome of an idempotent Green Points credit."""

    credited: bool
    entryId: str | None
    points: int


@dataclass(frozen=True)
class RedemptionResult:
    """Outcome of an atomic redemption."""

    redeemed: bool
    redemptionId: str | None
    pointsRedeemed: int
    amazonPayCreditedMinor: int
    newBalance: int
    reason: str | None = None


# ---------------------------------------------------------------------------
# 1. Compare-and-set marketplace listing claim (R6.5)
# ---------------------------------------------------------------------------


def claim_listing(session: Session, listingId: str, buyerId: str) -> ClaimResult:
    """Atomically claim an ACTIVE listing for ``buyerId``.

    Performs ``UPDATE ... SET status='SOLD', buyerId=:b WHERE listingId=:id AND
    status='ACTIVE'`` and reports whether this caller won the row. Among
    concurrent callers exactly one observes ``claimed=True`` (R6.5).

    The caller is responsible for committing the surrounding transaction (or
    using :func:`session_scope`).
    """

    result = session.execute(
        sa.update(MarketplaceListing)
        .where(
            MarketplaceListing.listingId == listingId,
            MarketplaceListing.status == ListingStatus.ACTIVE,
        )
        .values(status=ListingStatus.SOLD, buyerId=buyerId)
    )
    claimed = result.rowcount == 1
    return ClaimResult(claimed=claimed, listingId=listingId, buyerId=buyerId if claimed else None)


# ---------------------------------------------------------------------------
# 2. Idempotent single refund per return request (R10.1)
# ---------------------------------------------------------------------------


def issue_refund_once(
    session: Session,
    returnRequestId: str,
    amountMinor: int,
    currency: str,
    paymentMethod: PaymentMethod,
    triggeringDisposition: Disposition | None = None,
    atozApplied: bool = False,
    status: RefundStatus = RefundStatus.SUCCEEDED,
) -> RefundResult:
    """Create the single refund row for ``returnRequestId`` if none exists.

    The unique constraint on ``Refund.returnRequestId`` guarantees at most one
    refund per return request (R10.1). A second call for the same request is a
    no-op and returns the existing refund's id with ``created=False``.
    """

    existing = session.scalar(
        sa.select(Refund).where(Refund.returnRequestId == returnRequestId)
    )
    if existing is not None:
        return RefundResult(created=False, refundId=existing.refundId)

    refund = Refund(
        refundId=_new_id("rf"),
        returnRequestId=returnRequestId,
        amountMinor=amountMinor,
        currency=currency,
        triggeringDisposition=triggeringDisposition,
        paymentMethod=paymentMethod,
        atozApplied=atozApplied,
        status=status,
        attemptCount=1,
        completedAt=utc_now() if status == RefundStatus.SUCCEEDED else None,
    )
    session.add(refund)
    try:
        session.flush()
    except IntegrityError:
        # Lost a race: another transaction inserted the unique row first.
        session.rollback()
        existing = session.scalar(
            sa.select(Refund).where(Refund.returnRequestId == returnRequestId)
        )
        return RefundResult(created=False, refundId=existing.refundId if existing else "")
    return RefundResult(created=True, refundId=refund.refundId)


# ---------------------------------------------------------------------------
# 3. Idempotent Green Points credit per (returnRequestId, type) (R8.6)
# ---------------------------------------------------------------------------


def credit_green_points_once(
    session: Session,
    customerId: str,
    returnRequestId: str,
    credit_type: GreenPointsType,
    points: int,
    disposition: Disposition | None = None,
) -> CreditResult:
    """Credit Green Points at most once per ``(returnRequestId, type)`` (R8.6).

    Inserts a ledger entry and increments the customer's balance in the same
    transaction. A duplicate ``(returnRequestId, type)`` is rejected by the
    unique constraint and reported with ``credited=False`` without changing the
    balance. ``points`` must be a non-negative integer; a zero credit (e.g.
    warehouse) still records a ledger entry but leaves the balance unchanged.
    """

    if points < 0:
        raise ValueError("points must be non-negative")

    existing = session.scalar(
        sa.select(GreenPointsLedger).where(
            GreenPointsLedger.returnRequestId == returnRequestId,
            GreenPointsLedger.type == credit_type,
        )
    )
    if existing is not None:
        return CreditResult(credited=False, entryId=existing.entryId, points=existing.points)

    entry = GreenPointsLedger(
        entryId=_new_id("gpl"),
        customerId=customerId,
        returnRequestId=returnRequestId,
        type=credit_type,
        points=points,
        disposition=disposition,
    )
    session.add(entry)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            sa.select(GreenPointsLedger).where(
                GreenPointsLedger.returnRequestId == returnRequestId,
                GreenPointsLedger.type == credit_type,
            )
        )
        return CreditResult(
            credited=False,
            entryId=existing.entryId if existing else None,
            points=existing.points if existing else 0,
        )

    _increment_balance(session, customerId, points)
    return CreditResult(credited=True, entryId=entry.entryId, points=points)


def _increment_balance(session: Session, customerId: str, delta: int) -> int:
    """Increment (or create) a customer's Green Points balance; return new total."""

    balance = session.get(GreenPointsBalance, customerId)
    if balance is None:
        balance = GreenPointsBalance(customerId=customerId, balance=0)
        session.add(balance)
        session.flush()
    balance.balance += delta
    session.flush()
    return balance.balance


# ---------------------------------------------------------------------------
# 4. Atomic redemption: deduct points + credit Amazon Pay (R9.2)
# ---------------------------------------------------------------------------


def redeem_points(
    session: Session,
    customerId: str,
    points: int,
    conversion_rate_minor_per_point: int,
) -> RedemptionResult:
    """Atomically redeem ``points`` to the Amazon Pay wallet (R9.2, R9.6).

    Validates the request, then within a single transaction deducts the points
    from the balance and credits the equivalent Amazon Pay amount, recording a
    :class:`RedemptionRecord`. Either both effects apply or neither does. The
    balance never goes negative.

    Args:
        points: Whole number of points to redeem; must be ``>= 1`` and
            ``<= balance`` (R9.1, R9.4).
        conversion_rate_minor_per_point: Minor units credited per point.

    Returns:
        A :class:`RedemptionResult`. ``redeemed=False`` with a ``reason`` when
        validation fails (no state change).
    """

    if not isinstance(points, int) or isinstance(points, bool) or points < 1:
        return RedemptionResult(
            redeemed=False,
            redemptionId=None,
            pointsRedeemed=0,
            amazonPayCreditedMinor=0,
            newBalance=_current_balance(session, customerId),
            reason="points must be a whole number >= 1",
        )

    balance_row = session.get(GreenPointsBalance, customerId)
    current = balance_row.balance if balance_row is not None else 0
    if points > current:
        return RedemptionResult(
            redeemed=False,
            redemptionId=None,
            pointsRedeemed=0,
            amazonPayCreditedMinor=0,
            newBalance=current,
            reason=f"insufficient balance; available {current}",
        )

    credited_minor = points * conversion_rate_minor_per_point

    # Deduct points.
    balance_row.balance = current - points

    # Credit Amazon Pay wallet (create the row if absent).
    wallet = session.get(AmazonPayBalance, customerId)
    if wallet is None:
        wallet = AmazonPayBalance(customerId=customerId, balanceMinor=0, currency="INR")
        session.add(wallet)
        session.flush()
    wallet.balanceMinor += credited_minor

    record = RedemptionRecord(
        redemptionId=_new_id("rd"),
        customerId=customerId,
        pointsRedeemed=points,
        amazonPayCreditedMinor=credited_minor,
        conversionRate=conversion_rate_minor_per_point,
    )
    session.add(record)
    session.flush()

    return RedemptionResult(
        redeemed=True,
        redemptionId=record.redemptionId,
        pointsRedeemed=points,
        amazonPayCreditedMinor=credited_minor,
        newBalance=balance_row.balance,
    )


def _current_balance(session: Session, customerId: str) -> int:
    """Return the customer's current Green Points balance (0 if none)."""

    row = session.get(GreenPointsBalance, customerId)
    return row.balance if row is not None else 0


__all__ = [
    "get_engine",
    "get_session_factory",
    "init_db",
    "create_all",
    "drop_all",
    "session_scope",
    "ClaimResult",
    "RefundResult",
    "CreditResult",
    "RedemptionResult",
    "claim_listing",
    "issue_refund_once",
    "credit_green_points_once",
    "redeem_points",
]
