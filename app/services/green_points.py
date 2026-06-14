"""Green_Points_Service — credit (once), balance, atomic redemption (R8, R9).

Implements the design "Green_Points_Service" section and Requirements 8 and 9.

Crediting (R8)
--------------
``credit(returnRequestId, disposition)`` credits the configured integer amount
(``>= 1``) for the disposition **at most once per return request** (R8.1, R8.2,
R8.3, R8.6), records the disposition and return request (R8.3, R8.4), credits
**zero** for a warehouse return (R8.5/R8.6), and leaves the balance unchanged /
retry-eligible on failure (R8.7). The customer balance is always a non-negative
integer initialised to 0 (R8.4).

Disposition → (Green Points type, configured amount) mapping
------------------------------------------------------------
The :class:`~app.domain.models.GreenPointsType` enum defines only
``CREDIT_RESALE``, ``CREDIT_DONATION``, and ``REDEEM``. There is no dedicated
``CREDIT_KEEP_IT`` member, so a **Keep It** acceptance is recorded as a
**resale-type credit** (``GreenPointsType.CREDIT_RESALE``) using the configured
``green_points_keep_it`` amount. This is safe for the at-most-once guarantee:
the unique constraint is on ``(returnRequestId, type)`` and a return request can
only ever yield a single disposition, so no return request can collide on the
shared ``CREDIT_RESALE`` type. The recorded ``disposition`` column
(``KEEP_IT`` vs ``HYPERLOCAL_RESALE``) preserves the true provenance (R8.4).

Redemption (R9)
---------------
``redeem(customerId, points)`` validates that ``points`` is a whole number
``>= 1`` and ``<= balance`` (R9.1, R9.3, R9.4); on a valid request it credits
the configured-rate Amazon Pay amount and deducts the points as a single atomic
operation via :func:`app.domain.repository.redeem_points` (R9.2), recording the
points / credited amount / timestamp (R9.6). Crediting Amazon Pay is modelled
through an **injectable gateway seam** (``gateway(customerId, creditedMinor) ->
bool``); when it reports failure the redemption is rejected and the balance is
left unchanged (R9.5) — neither side effect is applied.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    AmazonPayBalance,
    Customer,
    Disposition,
    GreenPointsBalance,
    GreenPointsLedger,
    GreenPointsType,
)
from app.domain.repository import (
    CreditResult,
    RedemptionResult,
    credit_green_points_once,
    get_session_factory,
    redeem_points,
)
from app.fixtures.seed_data import GLOBAL_CONFIG
from app.services.return_initiation import get_db

__all__ = [
    "router",
    "CREDIT_DISPOSITION_MAP",
    "PointsGateway",
    "credit",
    "redeem",
    "get_balance",
    "configured_credit_amount",
]


# ---------------------------------------------------------------------------
# Disposition → (GreenPointsType, GLOBAL_CONFIG amount key) mapping
# ---------------------------------------------------------------------------
#
# Keep It is recorded as a resale-type credit (see module docstring). Warehouse
# is handled separately as an explicit zero credit (R8.5/R8.6) and is therefore
# intentionally absent from this map.
CREDIT_DISPOSITION_MAP: dict[Disposition, tuple[GreenPointsType, str]] = {
    Disposition.HYPERLOCAL_RESALE: (GreenPointsType.CREDIT_RESALE, "green_points_resale"),
    Disposition.GREEN_DONATION: (GreenPointsType.CREDIT_DONATION, "green_points_donation"),
    Disposition.KEEP_IT: (GreenPointsType.CREDIT_RESALE, "green_points_keep_it"),
}

#: An Amazon Pay credit gateway: ``gateway(customerId, creditedMinor) -> bool``.
PointsGateway = Callable[[str, int], bool]


def _default_pay_gateway(_customerId: str, _creditedMinor: int) -> bool:
    """Default Amazon Pay gateway used in STUB_MODE — always succeeds."""

    return True


def configured_credit_amount(disposition: Disposition, config: dict | None = None) -> int:
    """Return the configured Green Points amount for ``disposition``.

    Warehouse returns credit zero (R8.5); resale/donation/Keep It credit the
    configured integer amount (``>= 1``, R8.1-8.3).
    """

    cfg = config or GLOBAL_CONFIG
    if disposition == Disposition.WAREHOUSE_RETURN:
        return 0
    mapping = CREDIT_DISPOSITION_MAP.get(disposition)
    if mapping is None:
        raise ValueError(f"No Green Points credit configured for {disposition!r}")
    _type, key = mapping
    amount = int(cfg[key])
    if amount < 1:
        raise ValueError(
            f"Configured Green Points amount for {disposition!r} must be >= 1"
        )
    return amount


# ---------------------------------------------------------------------------
# Crediting (R8)
# ---------------------------------------------------------------------------


def credit(
    session: Session,
    customerId: str,
    returnRequestId: str,
    disposition: Disposition,
    *,
    config: dict | None = None,
) -> CreditResult:
    """Credit the configured Green Points for ``disposition`` at most once (R8).

    Args:
        session: Open session; the caller controls the transaction boundary.
        customerId: Customer whose balance is credited.
        returnRequestId: Return request that generated the credit (recorded).
        disposition: The selected disposition. Resale/donation/Keep It credit
            the configured integer amount (``>= 1``); a warehouse return credits
            zero (R8.5) and is a no-op against the balance.
        config: Optional config override (defaults to ``GLOBAL_CONFIG``).

    Returns:
        A :class:`~app.domain.repository.CreditResult`. For a warehouse return
        the result reports ``credited=False`` with ``points=0`` and the balance
        is unchanged.
    """

    cfg = config or GLOBAL_CONFIG

    # Warehouse returns credit zero Green Points (R8.5, R8.6) — no ledger entry,
    # balance left unchanged.
    if disposition == Disposition.WAREHOUSE_RETURN:
        return CreditResult(credited=False, entryId=None, points=0)

    credit_type, _key = CREDIT_DISPOSITION_MAP[disposition]
    amount = configured_credit_amount(disposition, cfg)

    # Atomic, unique-guarded credit per (returnRequestId, type) (R8.6). On a
    # duplicate the repository reports credited=False and leaves the balance
    # unchanged; on any failure the surrounding transaction rolls back, keeping
    # the request retry-eligible (R8.7).
    return credit_green_points_once(
        session,
        customerId=customerId,
        returnRequestId=returnRequestId,
        credit_type=credit_type,
        points=amount,
        disposition=disposition,
    )


# ---------------------------------------------------------------------------
# Balance (R8.4)
# ---------------------------------------------------------------------------


def get_balance(session: Session, customerId: str) -> int:
    """Return the customer's current Green Points balance (integer ``>= 0``).

    The balance is initialised to 0 before any Green Points are credited (R8.4).
    """

    row = session.get(GreenPointsBalance, customerId)
    return row.balance if row is not None else 0


# ---------------------------------------------------------------------------
# Redemption (R9)
# ---------------------------------------------------------------------------


def _whole_points(points: object) -> int | None:
    """Coerce ``points`` to a whole integer, or ``None`` if not a whole number.

    Booleans, non-numeric values, and fractional floats return ``None`` (R9.4).
    A whole-valued float such as ``5.0`` is accepted and returned as ``5``.
    """

    if isinstance(points, bool):
        return None
    if isinstance(points, int):
        return points
    if isinstance(points, float):
        if not points.is_integer():
            return None
        return int(points)
    return None


def redeem(
    session: Session,
    customerId: str,
    points: object,
    *,
    conversion_rate: int | None = None,
    gateway: PointsGateway | None = None,
    config: dict | None = None,
) -> RedemptionResult:
    """Atomically redeem ``points`` to the Amazon Pay wallet (R9).

    Validation (R9.1, R9.3, R9.4): ``points`` must be a whole number ``>= 1``
    and ``<= balance``. Any zero, negative, fractional, or over-balance request
    is rejected with the balance unchanged (and an available-balance message for
    over-balance, R9.3).

    On a valid request the configured-rate Amazon Pay amount is credited and the
    points are deducted as a single atomic operation (R9.2); the redeemed
    points, credited amount, and timestamp are recorded (R9.6). The Amazon Pay
    credit itself is modelled by an injectable ``gateway`` seam — when it reports
    failure the redemption is rejected and the balance is left unchanged (R9.5),
    so neither the deduction nor the credit is applied.

    Returns:
        A :class:`~app.domain.repository.RedemptionResult`.
    """

    cfg = config or GLOBAL_CONFIG
    rate = (
        conversion_rate
        if conversion_rate is not None
        else int(cfg["conversion_rate_points_to_minor"])
    )
    gateway = gateway or _default_pay_gateway

    current = get_balance(session, customerId)

    # R9.4: reject zero, negative, fractional, or non-numeric amounts.
    whole = _whole_points(points)
    if whole is None or whole < 1:
        return RedemptionResult(
            redeemed=False,
            redemptionId=None,
            pointsRedeemed=0,
            amazonPayCreditedMinor=0,
            newBalance=current,
            reason="The requested redemption amount is invalid; it must be a "
            "whole number of at least 1 Green Point.",
        )

    # R9.3: reject over-balance with the available-balance message.
    if whole > current:
        return RedemptionResult(
            redeemed=False,
            redemptionId=None,
            pointsRedeemed=0,
            amazonPayCreditedMinor=0,
            newBalance=current,
            reason=f"Insufficient Green Points balance; available balance is "
            f"{current}.",
        )

    # R9.5: Amazon Pay credit failure seam — leave the balance unchanged.
    credited_minor = whole * rate
    try:
        accepted = gateway(customerId, credited_minor)
    except Exception:
        accepted = False
    if not accepted:
        return RedemptionResult(
            redeemed=False,
            redemptionId=None,
            pointsRedeemed=0,
            amazonPayCreditedMinor=0,
            newBalance=current,
            reason="The redemption could not be completed; your Green Points "
            "balance is unchanged.",
        )

    # R9.2/R9.6: atomic deduct + Amazon Pay credit + record.
    return redeem_points(
        session,
        customerId=customerId,
        points=whole,
        conversion_rate_minor_per_point=rate,
    )


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["green-points"])


def _ensure_customer(session: Session, customerId: str) -> None:
    """Raise 404 if the customer does not exist."""

    if session.get(Customer, customerId) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "CUSTOMER_NOT_FOUND",
                "message": f"Customer {customerId} not found.",
            },
        )


@router.get("/customers/{customerId}/green-points")
def get_green_points(customerId: str, session: Session = Depends(get_db)) -> dict:
    """Return the customer's current Green Points balance (R8.4)."""

    _ensure_customer(session, customerId)
    return {"customerId": customerId, "balance": get_balance(session, customerId)}


@router.get("/customers/{customerId}/green-points/history")
def get_green_points_history(customerId: str, session: Session = Depends(get_db)) -> dict:
    """Return the customer's Green Points ledger + sustainability achievements.

    All figures are derived from the customer's real ledger entries — no
    fabricated platform-wide statistics.
    """

    _ensure_customer(session, customerId)
    entries = session.scalars(
        select(GreenPointsLedger)
        .where(GreenPointsLedger.customerId == customerId)
        .order_by(GreenPointsLedger.createdAt.desc())
    ).all()

    history = [
        {
            "type": e.type.value,
            "points": e.points,
            "disposition": e.disposition.value if e.disposition is not None else None,
            "returnRequestId": e.returnRequestId,
            "createdAt": e.createdAt.isoformat() if e.createdAt is not None else None,
        }
        for e in entries
    ]

    # Achievements: counts of real second-life resolutions for this customer.
    rescued = sum(1 for e in entries if e.type != GreenPointsType.REDEEM and e.points > 0)
    donations = sum(1 for e in entries if e.disposition == Disposition.GREEN_DONATION)
    resold = sum(1 for e in entries if e.disposition == Disposition.HYPERLOCAL_RESALE)
    kept = sum(1 for e in entries if e.disposition == Disposition.KEEP_IT)
    earned = sum(e.points for e in entries if e.type != GreenPointsType.REDEEM and e.points > 0)

    return {
        "customerId": customerId,
        "history": history,
        "achievements": {
            "productsRescued": rescued,
            "donationsMade": donations,
            "itemsResold": resold,
            "itemsKept": kept,
            "wastePreventedItems": donations + resold + kept,
            "totalEarned": earned,
        },
    }


@router.post("/customers/{customerId}/green-points/redeem")
def post_redeem(customerId: str, body: dict, session: Session = Depends(get_db)) -> dict:
    """Redeem Green Points to Amazon Pay (R9.1-9.6).

    Accepts ``{"points": <number>}``. Validation (whole number, ``>= 1``,
    ``<= balance``) is performed by :func:`redeem` so the precise R9.3/R9.4
    rejection messages are surfaced.
    """

    _ensure_customer(session, customerId)
    points = body.get("points") if isinstance(body, dict) else None

    result = redeem(session, customerId, points)
    if not result.redeemed:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "REDEMPTION_REJECTED",
                "message": result.reason,
                "balance": result.newBalance,
            },
        )

    return {
        "redemptionId": result.redemptionId,
        "customerId": customerId,
        "pointsRedeemed": result.pointsRedeemed,
        "amazonPayCreditedMinor": result.amazonPayCreditedMinor,
        "balance": result.newBalance,
    }
