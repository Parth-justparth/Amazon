"""Keep_It_Service — bounded partial-refund "Keep It" offer (R11).

Implements the design "Keep_It_Service" section and Requirement 11, and
contributes to Requirements 8.3 (Green Points credit recorded with disposition)
and 12.1 (Carbon_Savings on acceptance).

Placement in the lifecycle
---------------------------
After scoring (and DOA if required) and **before** normal disposition routing,
:func:`evaluate_keep_it` decides whether to present a ``Keep_It_Offer``:

* **Trigger (R11.1):** the recorded ``reason`` is a ``Minor_Issue_Reason``
  (``MINOR_DEFECT`` or ``COLOR_APPEARANCE_NOT_AS_EXPECTED``) **and** the
  ``secondLifeScore >= keep_it_min_score`` (a configurable integer in
  ``[0, 100]``) **and** a bounded positive ``Partial_Refund_Amount`` exists.
* When triggered, a :class:`~app.domain.models.KeepItOffer` is persisted in the
  ``PRESENTED`` state with ``expiresAt = now + keep_it_response_window_hours``
  and the request moves to ``KEEP_IT_OFFERED``.
* Otherwise Keep It is skipped and the request routes straight to the
  :mod:`Decision_Engine <app.services.decision_engine>`.

Bounded Partial_Refund_Amount (R11.2, R11.3)
--------------------------------------------
Let ``P`` = purchase price, ``RLC`` = reverse-logistics cost a standard return
would incur, ``DIV`` = retained depreciated item value (the item the customer
keeps). The amount is computed from a configurable factor and then **clamped**
so every bound holds simultaneously::

    raw    = round(keep_it_refund_factor * P)
    cap    = RLC - DIV                  # net-profit cap: A + DIV <= RLC (R11.3)
    upper  = min(P - 1, RLC - 1, cap)   # strict safety bounds (R11.2)
    amount = max(1, min(raw, upper))

The offer is presented **only if** ``upper >= 1`` (a positive amount satisfying
every bound exists, which requires ``cap >= 1``, i.e. ``RLC > DIV``); otherwise
:func:`compute_partial_refund_amount` returns ``None`` and Keep It is skipped.
This guarantees simultaneously ``A > 0``, ``A < P``, ``A < RLC``, and
``A + DIV <= RLC`` — the company stays in net profit.

Acceptance side-effects (R11.5, R11.8, R11.9, R8.3, R12.1)
----------------------------------------------------------
On accept :func:`accept_offer` issues a ``Partial_Refund`` equal to the bounded
amount via :mod:`Refund_Service <app.services.refund>` (disposition
``KEEP_IT``), credits the configured Keep It Green Points via
:mod:`Green_Points_Service <app.services.green_points>`, and computes the
``Carbon_Savings`` via :mod:`Carbon_Savings_Service
<app.services.carbon_savings>`. It does **not** generate a shipping label or
initiate return logistics. Both financial side-effects are bounded to **at most
once** per return request by the underlying unique-constraint idempotency
(``Refund.returnRequestId`` and ``(returnRequestId, type)`` on the points
ledger), so a repeated accept is a safe no-op. The offer state, partial-refund
amount, response timestamp, and (via ``returnRequestId``) the accepting customer
are recorded as the Keep It audit trail (R11.8).

Decline / expiry (R11.6, R11.7)
-------------------------------
:func:`decline_offer` (explicit decline) and :func:`expire_offer` (no response
within the configured window of ``>= 1`` hour, invoked by the scheduler in
task 21) set the offer to ``DECLINED`` / ``EXPIRED`` and route to the
``Decision_Engine`` with ``KEEP_IT`` excluded, yielding a disposition among
Warehouse / Resale / Donation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Disposition,
    KeepItOffer,
    KeepItOfferState,
    MINOR_ISSUE_REASONS,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)
from app.domain.money import utc_now
from app.services import green_points
from app.services.carbon_savings import (
    ImpactResult,
    MissingCO2FactorError,
    resolve_impact,
)
from app.services.decision_engine import (
    DecisionOutcome,
    GuardrailConfig,
    _latest_score,
    re_evaluate,
)
from app.fixtures.seed_data import GLOBAL_CONFIG
from app.services.refund import RefundOutcome, issue_refund
from app.services.return_initiation import get_db

__all__ = [
    "router",
    "compute_partial_refund_amount",
    "is_keep_it_eligible",
    "evaluate_keep_it",
    "accept_offer",
    "decline_offer",
    "expire_offer",
    "KeepItEvaluation",
    "AcceptOutcome",
]


# ---------------------------------------------------------------------------
# Pure math: bounds + eligibility (R11.1, R11.2, R11.3)
# ---------------------------------------------------------------------------


def compute_partial_refund_amount(
    price: int,
    rlc: int,
    div: int,
    *,
    config: dict | None = None,
) -> int | None:
    """Compute the bounded ``Partial_Refund_Amount`` (R11.2, R11.3).

    Args:
        price: Purchase price ``P`` in integer minor units.
        rlc: Reverse-logistics cost ``RLC`` a standard return would incur.
        div: Retained depreciated item value ``DIV`` the customer keeps.
        config: Optional config override (defaults to ``GLOBAL_CONFIG``); reads
            ``keep_it_refund_factor``.

    Returns:
        The clamped amount ``A`` satisfying ``A > 0``, ``A < P``, ``A < RLC``,
        and ``A + DIV <= RLC`` simultaneously, or ``None`` when no positive
        amount satisfies every bound (``upper < 1``, i.e. ``RLC <= DIV`` or the
        item is too cheap to leave the company in net profit).
    """

    cfg = config or GLOBAL_CONFIG
    factor = float(cfg["keep_it_refund_factor"])

    raw = round(factor * int(price))
    cap = int(rlc) - int(div)  # net-profit cap (R11.3)
    upper = min(int(price) - 1, int(rlc) - 1, cap)  # strict upper bounds (R11.2)

    # Present only if a positive amount satisfying every bound exists.
    if upper < 1:
        return None
    return max(1, min(raw, upper))


def is_keep_it_eligible(
    reason: ReturnReason,
    score: int | None,
    *,
    config: dict | None = None,
) -> bool:
    """Whether the reason + score qualify for a Keep It offer (R11.1).

    Eligible iff ``reason`` is a ``Minor_Issue_Reason`` and ``score`` is present
    and ``>= keep_it_min_score`` (a configurable integer in ``[0, 100]``).
    """

    cfg = config or GLOBAL_CONFIG
    if score is None:
        return False
    if reason not in MINOR_ISSUE_REASONS:
        return False
    min_score = int(cfg["keep_it_min_score"])
    return int(score) >= min_score


# ---------------------------------------------------------------------------
# Economics resolution (reuse the Decision_Engine pure functions)
# ---------------------------------------------------------------------------


def _economics(rr: ReturnRequest, score: int) -> tuple[int, int]:
    """Return ``(reverse_logistics_cost, depreciated_item_value)`` for ``rr``.

    Reuses the :mod:`Decision_Engine <app.services.decision_engine>` pure
    economics functions so Keep It bounds and the downstream disposition share
    one definition of ``RLC`` / ``DIV``.
    """

    from app.services.decision_engine import compute_economics

    return compute_economics(
        rr.itemCategory, rr.weightGrams, rr.purchasePriceMinor, score
    )


# ---------------------------------------------------------------------------
# Evaluation result carriers
# ---------------------------------------------------------------------------


@dataclass
class KeepItEvaluation:
    """Outcome of :func:`evaluate_keep_it`.

    ``presented`` is ``True`` when a Keep It offer was created/persisted (the
    request is now ``KEEP_IT_OFFERED``); ``offer`` then carries the row. When
    ``presented`` is ``False`` the request was routed to the Decision_Engine and
    ``decision`` carries that outcome.
    """

    presented: bool
    offer: KeepItOffer | None = None
    decision: DecisionOutcome | None = None


@dataclass
class AcceptOutcome:
    """Outcome of :func:`accept_offer`."""

    offer: KeepItOffer
    refund: RefundOutcome
    points_credited: int
    impact: ImpactResult | None = None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _get_offer(session: Session, return_request_id: str) -> KeepItOffer | None:
    return session.scalar(
        select(KeepItOffer).where(KeepItOffer.returnRequestId == return_request_id)
    )


# ---------------------------------------------------------------------------
# Evaluation: present an offer or route to the Decision_Engine (R11.1)
# ---------------------------------------------------------------------------


def evaluate_keep_it(
    session: Session,
    rr: ReturnRequest,
    *,
    score: int | None = None,
    config: dict | None = None,
    now: datetime | None = None,
    client=None,
    guardrail: GuardrailConfig | None = None,
) -> KeepItEvaluation:
    """Evaluate Keep It eligibility ahead of normal routing (R11.1, R11.2, R11.3).

    If the reason + score qualify (R11.1) **and** a bounded positive amount
    exists (R11.2, R11.3), a :class:`KeepItOffer` is created in the ``PRESENTED``
    state (``expiresAt = now + keep_it_response_window_hours``) and the request
    moves to ``KEEP_IT_OFFERED``. Otherwise the request is routed to the
    :mod:`Decision_Engine <app.services.decision_engine>` (Keep It skipped).

    Idempotent: if an offer already exists it is returned without creating a
    second one.

    Args:
        session: Open session; the caller controls the transaction boundary.
        rr: The scored return request.
        score: Optional explicit score override; defaults to the latest recorded
            ``secondLifeScore``.
        config: Optional config override (defaults to ``GLOBAL_CONFIG``).
        now: Injected clock for deterministic ``presentedAt`` / ``expiresAt``.
        client: Optional vision client forwarded to the Decision_Engine on the
            skip path (STUB_MODE in CI/demo).
        guardrail: Optional guardrail config forwarded to the Decision_Engine.

    Returns:
        A :class:`KeepItEvaluation`.
    """

    cfg = config or GLOBAL_CONFIG
    moment = now or utc_now()

    # Idempotency: a previously-presented offer is returned as-is.
    existing = _get_offer(session, rr.returnRequestId)
    if existing is not None:
        return KeepItEvaluation(presented=True, offer=existing)

    effective_score = (
        score if score is not None else _latest_score(session, rr.returnRequestId)
    )

    amount: int | None = None
    if is_keep_it_eligible(rr.reason, effective_score, config=cfg):
        # effective_score is guaranteed non-None by the eligibility check.
        rlc, div = _economics(rr, int(effective_score))
        amount = compute_partial_refund_amount(
            rr.purchasePriceMinor, rlc, div, config=cfg
        )

    if amount is None:
        # Not eligible, or no bounded positive amount exists -> skip Keep It and
        # route to the Decision_Engine for a normal disposition.
        decision = re_evaluate(
            session,
            rr,
            Disposition.KEEP_IT,
            client=client,
            guardrail=guardrail,
            score=effective_score,
        )
        return KeepItEvaluation(presented=False, decision=decision)

    window_hours = int(cfg["keep_it_response_window_hours"])
    offer = KeepItOffer(
        offerId=_new_id("ki"),
        returnRequestId=rr.returnRequestId,
        state=KeepItOfferState.PRESENTED,
        partialRefundAmountMinor=amount,
        currency=rr.currency,
        presentedAt=moment,
        expiresAt=moment + timedelta(hours=window_hours),
    )
    session.add(offer)
    rr.status = ReturnStatus.KEEP_IT_OFFERED
    session.flush()
    return KeepItEvaluation(presented=True, offer=offer)


# ---------------------------------------------------------------------------
# Accept (R11.5, R11.8, R11.9, R8.3, R12.1)
# ---------------------------------------------------------------------------


def accept_offer(
    session: Session,
    rr: ReturnRequest,
    *,
    refund_gateway=None,
    notifier=None,
    now: datetime | None = None,
    config: dict | None = None,
) -> AcceptOutcome:
    """Accept the Keep It offer and apply the bounded side-effects (R11.5).

    Issues a ``Partial_Refund`` equal to the offer amount via
    :func:`app.services.refund.issue_refund` (disposition ``KEEP_IT``), credits
    the configured Keep It Green Points via :func:`app.services.green_points`,
    and computes the ``Carbon_Savings`` via
    :func:`app.services.carbon_savings.resolve_impact` (R12.1). It does **not**
    generate a shipping label or initiate return logistics (R11.5).

    Both financial side-effects are bounded to at most once per return request
    by the underlying unique-constraint idempotency (R11.9, R10, R8): a repeated
    accept re-uses the existing refund/credit and applies no new money.

    The offer transitions to ``ACCEPTED`` with ``respondedAt`` recorded and the
    request to ``KEEP_IT_ACCEPTED``; together with the persisted
    ``partialRefundAmountMinor`` and the linked ``customerId`` this is the Keep
    It audit trail (R11.8).

    Raises:
        HTTPException: ``404`` if no offer exists; ``409`` if the offer is no
            longer in the ``PRESENTED`` state.
    """

    cfg = config or GLOBAL_CONFIG
    moment = now or utc_now()

    offer = _get_offer(session, rr.returnRequestId)
    if offer is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "KEEP_IT_OFFER_NOT_FOUND",
                "message": "No Keep It offer exists for this return request.",
            },
        )
    if offer.state != KeepItOfferState.PRESENTED:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "KEEP_IT_OFFER_RESOLVED",
                "message": (
                    "The Keep It offer has already been "
                    f"{offer.state.value.lower()}."
                ),
            },
        )

    # R11.5/R11.9: issue the partial refund (KEEP_IT). issue_refund enforces the
    # at-most-one-successful-refund guarantee per return request.
    refund = issue_refund(
        session,
        rr.returnRequestId,
        disposition=Disposition.KEEP_IT,
        amountMinor=offer.partialRefundAmountMinor,
        currency=offer.currency,
        paymentMethod=rr.paymentMethod,
        gateway=refund_gateway,
        notifier=notifier,
        now=moment,
    )

    # R8.3/R11.5: credit the configured Keep It Green Points (at most once per
    # (returnRequestId, type) via the unique-guarded ledger).
    credit_result = green_points.credit(
        session,
        rr.customerId,
        rr.returnRequestId,
        Disposition.KEEP_IT,
        config=cfg,
    )

    # R12.1: compute and record the Carbon_Savings for keeping the item. A
    # missing CO2 factor degrades gracefully to a money-only impact (R12.6).
    impact: ImpactResult | None
    try:
        impact = resolve_impact(session, rr, Disposition.KEEP_IT)
    except MissingCO2FactorError:
        impact = None

    # Record the Keep It outcome (R11.8). No shipping label / no logistics.
    offer.state = KeepItOfferState.ACCEPTED
    offer.respondedAt = moment
    rr.status = ReturnStatus.KEEP_IT_ACCEPTED
    session.flush()

    return AcceptOutcome(
        offer=offer,
        refund=refund,
        points_credited=credit_result.points if credit_result.credited else 0,
        impact=impact,
    )


# ---------------------------------------------------------------------------
# Decline / expiry -> route to the Decision_Engine excluding KEEP_IT (R11.6, R11.7)
# ---------------------------------------------------------------------------


def _resolve_and_route(
    session: Session,
    rr: ReturnRequest,
    offer: KeepItOffer,
    new_state: KeepItOfferState,
    *,
    now: datetime | None,
    client,
    guardrail: GuardrailConfig | None,
) -> DecisionOutcome:
    """Set the offer's terminal state and route to the Decision_Engine.

    ``re_evaluate`` adds ``KEEP_IT`` to ``excludedDispositions``, resets the
    request to ``SCORED``, and re-decides among Warehouse / Resale / Donation, so
    ``KEEP_IT`` is never reselected (R11.6, R11.7).
    """

    moment = now or utc_now()
    offer.state = new_state
    offer.respondedAt = moment
    session.flush()
    return re_evaluate(
        session, rr, Disposition.KEEP_IT, client=client, guardrail=guardrail
    )


def decline_offer(
    session: Session,
    rr: ReturnRequest,
    *,
    now: datetime | None = None,
    client=None,
    guardrail: GuardrailConfig | None = None,
) -> DecisionOutcome:
    """Decline the Keep It offer and route to the Decision_Engine (R11.6).

    Raises:
        HTTPException: ``404`` if no offer exists; ``409`` if the offer is no
            longer in the ``PRESENTED`` state.
    """

    offer = _get_offer(session, rr.returnRequestId)
    if offer is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "KEEP_IT_OFFER_NOT_FOUND",
                "message": "No Keep It offer exists for this return request.",
            },
        )
    if offer.state != KeepItOfferState.PRESENTED:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "KEEP_IT_OFFER_RESOLVED",
                "message": (
                    "The Keep It offer has already been "
                    f"{offer.state.value.lower()}."
                ),
            },
        )
    return _resolve_and_route(
        session,
        rr,
        offer,
        KeepItOfferState.DECLINED,
        now=now,
        client=client,
        guardrail=guardrail,
    )


def expire_offer(
    session: Session,
    rr: ReturnRequest,
    *,
    now: datetime | None = None,
    client=None,
    guardrail: GuardrailConfig | None = None,
) -> DecisionOutcome | None:
    """Expire an unanswered Keep It offer and route to the Decision_Engine (R11.7).

    Called by the scheduler (task 21) once the configured response window
    (``>= 1`` hour) has elapsed without a response. A no-op returning ``None``
    when the offer is missing or already resolved (so the scheduler is safe to
    re-run).
    """

    offer = _get_offer(session, rr.returnRequestId)
    if offer is None or offer.state != KeepItOfferState.PRESENTED:
        return None
    return _resolve_and_route(
        session,
        rr,
        offer,
        KeepItOfferState.EXPIRED,
        now=now,
        client=client,
        guardrail=guardrail,
    )


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["keep-it"])


def _serialize_offer(offer: KeepItOffer) -> dict:
    """Render a Keep It offer as the GET /returns/{id}/keep-it response (R11.4)."""

    return {
        "offerState": offer.state.value,
        "partialRefundAmount": offer.partialRefundAmountMinor,
        "currency": offer.currency,
    }


def _require_return(session: Session, returnRequestId: str) -> ReturnRequest:
    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "RETURN_NOT_FOUND", "message": "Return request not found."},
        )
    return rr


@router.get("/returns/{returnRequestId}/keep-it")
def get_keep_it(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Return the current Keep It offer, displaying the amount in order currency (R11.4)."""

    _require_return(session, returnRequestId)
    offer = _get_offer(session, returnRequestId)
    if offer is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "KEEP_IT_OFFER_NOT_FOUND",
                "message": "No Keep It offer exists for this return request.",
            },
        )
    return _serialize_offer(offer)


@router.post("/returns/{returnRequestId}/keep-it/accept")
def post_accept(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Accept the Keep It offer (partial refund + points + carbon, no logistics) (R11.5)."""

    rr = _require_return(session, returnRequestId)
    outcome = accept_offer(session, rr)
    payload = {
        "returnRequestId": rr.returnRequestId,
        "status": rr.status.value,
        "offerState": outcome.offer.state.value,
        "partialRefundAmount": outcome.offer.partialRefundAmountMinor,
        "currency": outcome.offer.currency,
        "refundStatus": outcome.refund.status.value,
        "pointsCredited": outcome.points_credited,
    }
    if outcome.impact is not None:
        payload["impactMessage"] = outcome.impact.impact_message
        payload["carbonSavingsKg"] = (
            float(outcome.impact.carbon_savings_kg)
            if outcome.impact.carbon_savings_kg is not None
            else None
        )
    return payload


@router.post("/returns/{returnRequestId}/keep-it/decline")
def post_decline(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Decline the Keep It offer and route to the Decision_Engine excluding KEEP_IT (R11.6)."""

    rr = _require_return(session, returnRequestId)
    outcome = decline_offer(session, rr)
    payload: dict = {
        "returnRequestId": rr.returnRequestId,
        "offerState": KeepItOfferState.DECLINED.value,
        "status": rr.status.value,
    }
    if outcome.ok and outcome.record is not None:
        payload["disposition"] = outcome.record.selected.value
        payload["decisionSource"] = outcome.record.decisionSource.value
    return payload
