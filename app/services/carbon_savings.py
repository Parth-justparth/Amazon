"""Carbon_Savings_Service — kg CO2 avoided + Impact_Message (R12).

Implements the design "Carbon_Savings_Service" section and Requirement 12.

Computation (R12.1, R12.2, R12.5)
---------------------------------
For a confirmed non-warehouse resolution the saved mass of CO2 is derived from
the configurable ``CO2_Factor`` rows (per disposition, per distance, per item
weight)::

    carbon_savings_kg = co2_factor_disposition[disposition]
                      + co2_factor_per_km * avoided_distance_km
                      + co2_factor_per_kg * (weightGrams / 1000)

All arithmetic uses :class:`~decimal.Decimal` (never float) so the stored value
is exact. The result is constrained to be ``>= 0``. A **Warehouse_Return_Flow**
resolution records exactly ``0`` kg (R12.5) without consulting the per-km /
per-kg factors — every avoided-logistics term is zero for a standard return.

Missing factor (R12.6)
----------------------
If any required ``CO2_Factor`` is unavailable, :func:`compute_carbon_savings`
raises :class:`MissingCO2FactorError` naming the missing ``factorKey``. The
service then records **no** carbon value (``carbonSavingsKg`` stays null) and the
``Impact_Message`` states only the money saved (no CO2 figure).

Impact_Message (R12.3)
----------------------
The rendered message always states the money saved in the order currency (via
:func:`app.domain.money.format_money`); when a carbon value is present it also
states the ``Carbon_Savings`` in kilograms of CO2.

Money-saved derivation (documented)
-----------------------------------
The "money saved" figure surfaced to the customer is the recorded
``purchasePriceMinor`` snapshot of the returned item — the value recovered for
the customer by resolving the return without a standard reverse-logistics trip.
This matches the design's worked example (``item_foot_01`` → ``moneySavedMinor
349900``). The endpoint keeps this deliberately simple; the precise correctness
target of this service is the CO2 computation, not the money figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    CO2Factor,
    Disposition,
    DispositionRecord,
    ReturnRequest,
    ReturnStatus,
)
from app.domain.money import format_money
from app.services.return_initiation import get_db

__all__ = [
    "router",
    "MissingCO2FactorError",
    "ImpactResult",
    "compute_carbon_savings",
    "format_co2",
    "build_impact_message",
    "resolve_impact",
    "DEFAULT_AVOIDED_DISTANCE_KM",
]


# ---------------------------------------------------------------------------
# Configuration / constants
# ---------------------------------------------------------------------------

#: Demo avoided-distance (km) per disposition. ``0`` for a warehouse return (no
#: reverse-logistics trip is avoided); the donation value reproduces the design
#: worked example (``item_foot_01``, 40 km → 7.225 kg). Kept simple for the demo
#: since there is no per-return distance field to read.
DEFAULT_AVOIDED_DISTANCE_KM: dict[Disposition, int] = {
    Disposition.WAREHOUSE_RETURN: 0,
    Disposition.HYPERLOCAL_RESALE: 15,
    Disposition.GREEN_DONATION: 40,
    Disposition.KEEP_IT: 40,
}

#: Customer-facing phrase per disposition, used in the Impact_Message.
_DISPOSITION_PHRASE: dict[Disposition, str] = {
    Disposition.WAREHOUSE_RETURN: "a standard return",
    Disposition.HYPERLOCAL_RESALE: "hyperlocal resale",
    Disposition.GREEN_DONATION: "donation",
    Disposition.KEEP_IT: "keeping your item",
}

#: Precision the carbon value is stored at (matches the Numeric(10, 3) column).
_STORE_QUANTUM = Decimal("0.001")
#: Precision the carbon value is rendered at in the Impact_Message.
_DISPLAY_QUANTUM = Decimal("0.01")


# ---------------------------------------------------------------------------
# Errors / results
# ---------------------------------------------------------------------------


class MissingCO2FactorError(Exception):
    """Raised when a required ``CO2_Factor`` row is unavailable (R12.6)."""

    def __init__(self, factor_key: str) -> None:
        self.factor_key = factor_key
        super().__init__(f"Required CO2_Factor '{factor_key}' is unavailable.")


@dataclass
class ImpactResult:
    """Outcome of resolving the impact card for a return request.

    ``carbon_savings_kg`` is ``None`` when a required factor was missing
    (R12.6); ``missing_factor`` then names the absent ``factorKey``.
    """

    money_saved_minor: int
    currency: str
    carbon_savings_kg: Decimal | None
    impact_message: str
    disposition: Disposition
    missing_factor: str | None = None


# ---------------------------------------------------------------------------
# Core computation (R12.1, R12.2, R12.5, R12.6)
# ---------------------------------------------------------------------------


def _load_factor(session: Session, factor_key: str) -> Decimal:
    """Return the Decimal value of a ``CO2_Factor`` row or raise if missing."""

    row = session.get(CO2Factor, factor_key)
    if row is None:
        raise MissingCO2FactorError(factor_key)
    return Decimal(row.value)


def compute_carbon_savings(
    session: Session,
    disposition: Disposition,
    weight_grams: int,
    avoided_distance_km: float | int | Decimal,
) -> Decimal:
    """Compute kg CO2 avoided for ``disposition`` (R12.1, R12.2, R12.5).

    Args:
        session: Open session used to read the ``CO2_Factor`` config rows.
        disposition: The confirmed resolution. ``WAREHOUSE_RETURN`` yields
            exactly ``0`` (R12.5); all other dispositions apply the full
            per-disposition + per-km + per-kg formula.
        weight_grams: The item weight in grams (converted to kilograms).
        avoided_distance_km: The reverse-logistics distance the resolution
            avoids, in kilometres.

    Returns:
        The carbon saved in kilograms of CO2 as an exact :class:`Decimal`,
        constrained to be ``>= 0``.

    Raises:
        MissingCO2FactorError: If a required ``CO2_Factor`` row is unavailable
            (R12.6). The missing ``factorKey`` is named on the exception.
    """

    # R12.5: a warehouse return avoids no reverse logistics — record exactly 0
    # without needing the per-km / per-kg factors.
    if disposition == Disposition.WAREHOUSE_RETURN:
        return Decimal("0")

    disposition_factor = _load_factor(session, f"disposition:{disposition.value}")
    per_km = _load_factor(session, "per_km")
    per_kg = _load_factor(session, "per_kg")

    distance = Decimal(str(avoided_distance_km))
    weight_kg = Decimal(int(weight_grams)) / Decimal(1000)

    result = disposition_factor + per_km * distance + per_kg * weight_kg

    # R12.1: the carbon savings is constrained to be >= 0.
    if result < 0:
        return Decimal("0")
    return result


# ---------------------------------------------------------------------------
# Rendering helpers (R12.3)
# ---------------------------------------------------------------------------


def format_co2(carbon_savings_kg: Decimal) -> str:
    """Render a carbon value as a fixed 2-decimal kg string (e.g. ``7.23``)."""

    quantized = carbon_savings_kg.quantize(_DISPLAY_QUANTUM, rounding=ROUND_HALF_UP)
    return f"{quantized}"


def build_impact_message(
    money_saved_minor: int,
    currency: str,
    carbon_savings_kg: Decimal | None,
    disposition: Disposition,
) -> str:
    """Build the customer-facing Impact_Message (R12.3, R12.6).

    Always states the money saved in the order currency. When
    ``carbon_savings_kg`` is provided it also states the carbon saved in kg of
    CO2; when ``None`` (missing-factor case, R12.6) only the money saved is
    shown.
    """

    money_str = format_money(money_saved_minor, currency)
    if carbon_savings_kg is None:
        return f"You saved {money_str}."

    co2_str = format_co2(carbon_savings_kg)
    phrase = _DISPOSITION_PHRASE.get(disposition, "this resolution")
    return f"You saved {money_str} and {co2_str} kg of CO2 by choosing {phrase}."


# ---------------------------------------------------------------------------
# Resolution + persistence (R12.3, R12.4, R12.6)
# ---------------------------------------------------------------------------


def _resolve_disposition(
    session: Session, return_request: ReturnRequest
) -> Disposition | None:
    """Determine the confirmed disposition for a return request, if any."""

    record = session.scalar(
        select(DispositionRecord).where(
            DispositionRecord.returnRequestId == return_request.returnRequestId
        )
    )
    if record is not None:
        return record.selected
    if return_request.status == ReturnStatus.KEEP_IT_ACCEPTED:
        return Disposition.KEEP_IT
    return None


def resolve_impact(
    session: Session,
    return_request: ReturnRequest,
    disposition: Disposition,
) -> ImpactResult:
    """Compute, persist, and render the impact card for a resolution (R12).

    On success the computed ``Carbon_Savings`` is recorded on the return request
    (R12.4) and a full Impact_Message is built (R12.3). If a required factor is
    missing the carbon value is left unrecorded (``None``) and a money-only
    message is produced (R12.6).
    """

    money_saved_minor = return_request.purchasePriceMinor
    currency = return_request.currency
    avoided = DEFAULT_AVOIDED_DISTANCE_KM.get(disposition, 0)

    try:
        carbon = compute_carbon_savings(
            session, disposition, return_request.weightGrams, avoided
        )
    except MissingCO2FactorError as exc:
        # R12.6: record no carbon value; display money saved only.
        return_request.carbonSavingsKg = None
        message = build_impact_message(money_saved_minor, currency, None, disposition)
        return ImpactResult(
            money_saved_minor=money_saved_minor,
            currency=currency,
            carbon_savings_kg=None,
            impact_message=message,
            disposition=disposition,
            missing_factor=exc.factor_key,
        )

    stored = carbon.quantize(_STORE_QUANTUM, rounding=ROUND_HALF_UP)
    return_request.carbonSavingsKg = stored  # R12.4
    message = build_impact_message(money_saved_minor, currency, carbon, disposition)
    return ImpactResult(
        money_saved_minor=money_saved_minor,
        currency=currency,
        carbon_savings_kg=stored,
        impact_message=message,
        disposition=disposition,
    )


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["carbon-savings"])


@router.get("/returns/{returnRequestId}/impact")
def get_impact(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Return the carbon-savings impact card for a resolved return (R12.3, R12.6).

    Response shape::

        {
          "moneySavedMinor": 349900,
          "currency": "INR",
          "carbonSavingsKg": 7.23,   # null / omitted when a factor is missing
          "impactMessage": "You saved \u20b93,499.00 and 7.23 kg of CO2 ..."
        }
    """

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "RETURN_NOT_FOUND", "message": "Return request not found."},
        )

    disposition = _resolve_disposition(session, rr)
    if disposition is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "DISPOSITION_PENDING",
                "message": "No disposition has been selected for this return yet.",
            },
        )

    result = resolve_impact(session, rr, disposition)

    payload: dict = {
        "moneySavedMinor": result.money_saved_minor,
        "currency": result.currency,
        "carbonSavingsKg": (
            float(result.carbon_savings_kg)
            if result.carbon_savings_kg is not None
            else None
        ),
        "impactMessage": result.impact_message,
    }
    if result.missing_factor is not None:
        # R12.6: surface an explicit computation-failure status naming the
        # missing CO2_Factor while still displaying the money saved (no CO2).
        payload["carbonStatus"] = "CARBON_COMPUTATION_FAILED"
        payload["missingFactor"] = result.missing_factor
    else:
        payload["carbonStatus"] = "OK"
    return payload
