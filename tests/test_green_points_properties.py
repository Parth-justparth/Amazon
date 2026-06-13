"""Property-based tests for the Green_Points_Service (task 10).

Covers design Correctness Properties 22, 23, 24, and 25 for
``app.services.green_points``. Each test is tagged with the exact
``Feature: secondlife-ai, Property {n}: {text}`` comment and a
``Validates: Requirements ...`` line, and runs against the Hypothesis ``ci``
profile (>= 100 examples; see ``tests/conftest.py``).

Every example builds a fresh in-memory SQLite database (tables only) and a
single customer, then exercises the pure service functions
(``credit`` / ``get_balance`` / ``redeem``) directly so the financial
invariants are asserted without any network or LLM dependency.
"""

from __future__ import annotations

import uuid

from hypothesis import assume, given
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import (
    AmazonPayBalance,
    Base,
    Customer,
    Disposition,
    GreenPointsLedger,
    RedemptionRecord,
)
from app.fixtures.seed_data import GLOBAL_CONFIG
from app.services.green_points import (
    configured_credit_amount,
    credit,
    get_balance,
    redeem,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Dispositions that earn a positive configured credit (R8.1, R8.2, R8.3).
_CREDIT_DISPOSITIONS = [
    Disposition.HYPERLOCAL_RESALE,
    Disposition.GREEN_DONATION,
    Disposition.KEEP_IT,
]
#: All dispositions a credit may be requested for (warehouse credits zero).
_ALL_DISPOSITIONS = _CREDIT_DISPOSITIONS + [Disposition.WAREHOUSE_RETURN]


def _make_session():
    """Create a disposable in-memory engine with tables and return a session."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return engine, factory()


def _add_customer(session, customerId: str = "cust_test") -> str:
    """Insert a customer with a zero-initialised balance and return its id."""

    session.add(Customer(customerId=customerId, name="Test", city="Bengaluru"))
    session.commit()
    return customerId


def _seed_balance(session, customerId: str, target: int) -> int:
    """Credit Green Points until the balance reaches at least ``target``.

    Uses distinct return requests so each credit succeeds. Returns the final
    balance (a multiple of the resale credit amount >= ``target``).
    """

    resale_amount = configured_credit_amount(Disposition.HYPERLOCAL_RESALE)
    while get_balance(session, customerId) < target:
        credit(
            session,
            customerId,
            f"rr_{uuid.uuid4().hex[:10]}",
            Disposition.HYPERLOCAL_RESALE,
        )
        session.commit()
    return get_balance(session, customerId)


def _is_valid_redemption(points: object, balance: int) -> bool:
    """Mirror the service's validity rule: whole number >= 1 and <= balance."""

    if isinstance(points, bool):
        return False
    if isinstance(points, int):
        whole = points
    elif isinstance(points, float):
        if not points.is_integer():
            return False
        whole = int(points)
    else:
        return False
    return whole >= 1 and whole <= balance


# ===========================================================================
# Property 22 — credited at most once with the configured amount
# ===========================================================================
# Feature: secondlife-ai, Property 22: Green Points credited at most once with the configured amount
# Validates: Requirements 5.6, 7.4, 8.1, 8.2, 8.3, 8.5, 8.6
@given(
    disposition=st.sampled_from(_ALL_DISPOSITIONS),
    attempts=st.integers(min_value=1, max_value=5),
    customer_suffix=st.integers(min_value=0, max_value=10**9),
)
def test_property_22_credit_at_most_once_configured_amount(
    disposition, attempts, customer_suffix
) -> None:
    engine, session = _make_session()
    try:
        # A generated customer id keeps the input space unbounded so Hypothesis
        # runs the full >= 100 examples rather than exhausting the disposition x
        # attempts grid.
        customerId = _add_customer(session, f"cust_{customer_suffix}")
        rrid = f"rr_{uuid.uuid4().hex[:10]}"

        # Repeatedly attempt to credit the same return request.
        for _ in range(attempts):
            credit(session, customerId, rrid, disposition)
            session.commit()

        balance = get_balance(session, customerId)
        ledger = (
            session.scalars(
                select(GreenPointsLedger).where(
                    GreenPointsLedger.returnRequestId == rrid
                )
            )
            .all()
        )

        if disposition == Disposition.WAREHOUSE_RETURN:
            # R8.5/R8.6: warehouse credits zero — no positive credit, no entry.
            assert balance == 0
            assert ledger == []
        else:
            expected = configured_credit_amount(disposition)
            assert expected >= 1  # configured integer >= 1 (R8.1-8.3)
            # At-most-once: exactly the configured amount regardless of attempts.
            assert balance == expected
            # Exactly one ledger entry recording disposition + return (R8.4, R8.6).
            assert len(ledger) == 1
            entry = ledger[0]
            assert entry.points == expected
            assert entry.disposition == disposition
            assert entry.returnRequestId == rrid
            assert entry.customerId == customerId
    finally:
        session.close()
        engine.dispose()


# ===========================================================================
# Property 23 — balance is always a non-negative integer
# ===========================================================================
# Feature: secondlife-ai, Property 23: Green Points balance is always a non-negative integer
# Validates: Requirements 8.4
@given(
    ops=st.lists(
        st.one_of(
            st.tuples(st.just("credit"), st.sampled_from(_ALL_DISPOSITIONS)),
            st.tuples(st.just("redeem"), st.integers(min_value=-50, max_value=3000)),
        ),
        min_size=0,
        max_size=25,
    )
)
def test_property_23_balance_non_negative_integer(ops) -> None:
    engine, session = _make_session()
    try:
        customerId = _add_customer(session)

        # Balance is initialised to 0 before any credit (R8.4).
        balance = get_balance(session, customerId)
        assert isinstance(balance, int) and balance == 0

        for kind, arg in ops:
            if kind == "credit":
                credit(session, customerId, f"rr_{uuid.uuid4().hex[:10]}", arg)
            else:  # redeem (default gateway succeeds; over-balance is rejected)
                redeem(session, customerId, arg)
            session.commit()

            balance = get_balance(session, customerId)
            assert isinstance(balance, int)
            assert balance >= 0
    finally:
        session.close()
        engine.dispose()


# ===========================================================================
# Property 24 — redemption validity
# ===========================================================================
# Feature: secondlife-ai, Property 24: Redemption validity
# Validates: Requirements 9.1, 9.3, 9.4
@given(
    seed_target=st.integers(min_value=0, max_value=2000),
    points=st.one_of(
        st.integers(min_value=-20, max_value=4000),
        st.floats(min_value=-20, max_value=4000, allow_nan=False, allow_infinity=False),
        st.booleans(),
    ),
)
def test_property_24_redemption_validity(seed_target, points) -> None:
    engine, session = _make_session()
    try:
        customerId = _add_customer(session)
        balance_before = _seed_balance(session, customerId, seed_target)

        expected_valid = _is_valid_redemption(points, balance_before)

        result = redeem(session, customerId, points)
        session.commit()
        balance_after = get_balance(session, customerId)

        # Accepted iff whole number >= 1 and <= balance (R9.1, R9.3, R9.4).
        assert result.redeemed == expected_valid
        if expected_valid:
            whole = int(points)
            assert balance_after == balance_before - whole
        else:
            # Rejected: balance left unchanged, with a reason message.
            assert balance_after == balance_before
            assert result.reason is not None
            assert result.pointsRedeemed == 0
    finally:
        session.close()
        engine.dispose()


# ===========================================================================
# Property 25 — redemption atomicity
# ===========================================================================
# Feature: secondlife-ai, Property 25: Redemption atomicity
# Validates: Requirements 9.2, 9.6
@given(
    seed_target=st.integers(min_value=1, max_value=2000),
    x_frac=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    rate=st.integers(min_value=1, max_value=1000),
    gateway_ok=st.booleans(),
)
def test_property_25_redemption_atomicity(seed_target, x_frac, rate, gateway_ok) -> None:
    engine, session = _make_session()
    try:
        customerId = _add_customer(session)
        balance_before = _seed_balance(session, customerId, seed_target)
        assume(balance_before >= 1)

        # Choose a valid X in [1, balance_before].
        x = max(1, min(balance_before, 1 + int(round(x_frac * (balance_before - 1)))))

        pay_before = 0  # wallet starts absent (== 0)
        gateway = (lambda _c, _m: gateway_ok)

        result = redeem(
            session, customerId, x, conversion_rate=rate, gateway=gateway
        )
        session.commit()

        balance_after = get_balance(session, customerId)
        wallet = session.get(AmazonPayBalance, customerId)
        pay_after = wallet.balanceMinor if wallet is not None else 0
        records = (
            session.scalars(
                select(RedemptionRecord).where(
                    RedemptionRecord.customerId == customerId
                )
            )
            .all()
        )

        if gateway_ok:
            # Both effects applied together (R9.2): -X points, +X*r minor units.
            assert result.redeemed is True
            assert balance_after == balance_before - x
            assert pay_after == pay_before + x * rate
            # A single record captures points, credited amount, timestamp (R9.6).
            assert len(records) == 1
            rec = records[0]
            assert rec.pointsRedeemed == x
            assert rec.amazonPayCreditedMinor == x * rate
            assert rec.completedAt is not None
        else:
            # Gateway failure seam (R9.5): neither effect applied.
            assert result.redeemed is False
            assert balance_after == balance_before
            assert pay_after == pay_before
            assert records == []
    finally:
        session.close()
        engine.dispose()
