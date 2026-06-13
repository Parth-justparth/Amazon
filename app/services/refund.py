"""Refund_Service — idempotent refunds, retries, timelines, PoD gate, A-to-z.

Implements the design "Refund_Service" section and Requirements 10, 11.5
(partial refund amount path), 17 (per-payment-method timelines), 18.6, and 19.4
(A-to-z platform refund).

Core guarantees
---------------
* **At most one successful refund per return request** (R10.1). Enforced by the
  unique constraint on ``Refund.returnRequestId`` plus a status check: once a
  refund has ``SUCCEEDED``, any subsequent attempt is rejected with a
  descriptive "already issued" message and the recorded amount is left
  unchanged (R10.5).
* **Order currency** (R10.2) and a full record of amount + return request +
  triggering disposition (R10.3).
* **Retry up to 3 attempts** against the payment gateway within a single issue
  call (R10.4); after 3 consecutive failures the return request is flagged
  ``MANUAL`` and the customer is notified (R10.6, R17.9).
* **Per-payment-method timeline** (R17.3-17.7): the ``expectedCompletionWindow``
  string is selected from :data:`PAYMENT_METHOD_WINDOWS` and recorded; the
  customer is notified of the window when the timeline begins (R17.8).
* **Timeline start event** (R17.1, R17.2): for ``WAREHOUSE_RETURN`` the timeline
  begins only after the warehouse quality check passes; for ``KEEP_IT``,
  ``HYPERLOCAL_RESALE``, and ``GREEN_DONATION`` it begins at the disposition
  confirmation event.
* **Pay-on-Delivery gate** (R17.10, R18.6): a PoD refund without accepted
  :class:`BankDetails` is withheld — the refund row enters
  ``WITHHELD_BANK_DETAILS``, the return request enters ``AWAITING_BANK_DETAILS``,
  no timeline starts, and a bank-details-required message is returned.
* **A-to-z support** (R19.4): :func:`issue_atoz_refund` issues a platform refund
  equal to the recorded purchase price with ``atozApplied = True``.

The payment gateway is injectable (a ``gateway`` callable) so the retry and
3-strike paths are deterministic under test. ``STUB_MODE`` callers that pass no
gateway get a default always-succeeds gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    BankDetails,
    Disposition,
    PaymentMethod,
    Refund,
    RefundStatus,
    ReturnRequest,
    ReturnStatus,
)
from app.domain.money import utc_now
from app.domain.repository import get_session_factory, issue_refund_once
from app.services.return_initiation import get_db

__all__ = [
    "router",
    "PAYMENT_METHOD_WINDOWS",
    "TIMELINE_WINDOWS",
    "expected_completion_window",
    "select_timeline_window",
    "timeline_starts_after_quality_check",
    "has_valid_bank_details",
    "RefundOutcome",
    "Gateway",
    "Notifier",
    "issue_refund",
    "issue_atoz_refund",
    "issue_platform_refund",
    "start_timeline",
    "MAX_REFUND_ATTEMPTS",
]


# ---------------------------------------------------------------------------
# Timeline window configuration (R17.3-17.7)
# ---------------------------------------------------------------------------

#: Expected refund-completion window per Payment_Method, recorded on the refund
#: as ``expectedCompletionWindow`` and surfaced to the customer (R17.8).
PAYMENT_METHOD_WINDOWS: dict[PaymentMethod, str] = {
    PaymentMethod.AMAZON_PAY_BALANCE: "within 2 hours",
    PaymentMethod.UPI: "2-4 business days",
    PaymentMethod.CARD: "3-5 business days",
    PaymentMethod.NET_BANKING: "2-10 business days",
    PaymentMethod.PAY_ON_DELIVERY: "2-4 business days",
}

#: Maximum gateway attempts per return request before flagging MANUAL (R10.4).
MAX_REFUND_ATTEMPTS: int = 3

#: A payment gateway: ``gateway(returnRequestId, attempt) -> bool`` (True = paid).
Gateway = Callable[[str, int], bool]

#: A customer notifier: ``notifier(event, payload)``.
Notifier = Callable[[str, dict], None]


def expected_completion_window(payment_method: PaymentMethod) -> str:
    """Return the configured expected-completion window for ``payment_method``."""

    return PAYMENT_METHOD_WINDOWS[payment_method]


#: Spec-naming aliases (design "Refund_Service" / task 9.4 wording).
TIMELINE_WINDOWS = PAYMENT_METHOD_WINDOWS
select_timeline_window = expected_completion_window


def timeline_starts_after_quality_check(disposition: Disposition | None) -> bool:
    """Whether the refund timeline starts only after warehouse quality check.

    ``True`` for ``WAREHOUSE_RETURN`` (R17.1); ``False`` for Keep It, resale, and
    donation, whose timeline starts at the disposition confirmation event
    (R17.2). A-to-z refunds (``disposition is None``) start at confirmation.
    """

    return disposition == Disposition.WAREHOUSE_RETURN


def _default_gateway(_returnRequestId: str, _attempt: int) -> bool:
    """Default payment gateway used in STUB_MODE — always succeeds."""

    return True


def _noop_notifier(_event: str, _payload: dict) -> None:
    """Default notifier — records nothing (callers may inject their own)."""

    return None


# ---------------------------------------------------------------------------
# Bank-details gate helper (R17.10, R18.6)
# ---------------------------------------------------------------------------


def has_valid_bank_details(session: Session, returnRequestId: str) -> bool:
    """Return whether accepted (valid) bank details exist for the return."""

    row = session.scalar(
        select(BankDetails).where(
            BankDetails.returnRequestId == returnRequestId,
            BankDetails.accepted.is_(True),
        )
    )
    return row is not None


# ---------------------------------------------------------------------------
# Outcome carrier
# ---------------------------------------------------------------------------


@dataclass
class RefundOutcome:
    """Result of a refund issue attempt.

    ``status`` mirrors the persisted :class:`RefundStatus`. ``already_issued`` is
    set when a prior successful refund caused this request to be rejected
    (R10.5). ``bank_details_required`` is set for the withheld PoD path
    (R17.10/R18.6). ``notifications`` lists the ``(event, payload)`` tuples
    emitted to the customer.
    """

    status: RefundStatus
    message: str
    refundId: str | None = None
    amountMinor: int | None = None
    currency: str | None = None
    expectedCompletionWindow: str | None = None
    timelineStarted: bool = False
    atozApplied: bool = False
    attemptCount: int = 0
    already_issued: bool = False
    bank_details_required: bool = False
    notifications: list[tuple[str, dict]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Refund row upsert (creation routed through repository.issue_refund_once)
# ---------------------------------------------------------------------------


def _get_refund(session: Session, returnRequestId: str) -> Refund | None:
    return session.scalar(
        select(Refund).where(Refund.returnRequestId == returnRequestId)
    )


def _ensure_refund_row(
    session: Session,
    returnRequestId: str,
    amountMinor: int,
    currency: str,
    paymentMethod: PaymentMethod,
    triggeringDisposition: Disposition | None,
    atozApplied: bool,
    initial_status: RefundStatus,
) -> Refund:
    """Return the single refund row, creating it via the repository if absent.

    Creation goes through :func:`issue_refund_once` so the unique-constraint
    idempotency guard (R10.1) is reused. An existing row (e.g. a withheld or
    previously-failed attempt) is returned as-is for the caller to transition.
    """

    existing = _get_refund(session, returnRequestId)
    if existing is not None:
        return existing

    issue_refund_once(
        session,
        returnRequestId=returnRequestId,
        amountMinor=amountMinor,
        currency=currency,
        paymentMethod=paymentMethod,
        triggeringDisposition=triggeringDisposition,
        atozApplied=atozApplied,
        status=initial_status,
    )
    row = _get_refund(session, returnRequestId)
    assert row is not None  # just created
    # issue_refund_once stamps a SUCCEEDED row with attemptCount=1/completedAt;
    # normalize for the PENDING/WITHHELD starting states this service drives.
    if initial_status != RefundStatus.SUCCEEDED:
        row.attemptCount = 0
        row.completedAt = None
        row.status = initial_status
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Timeline start (R17.1, R17.2, R17.8)
# ---------------------------------------------------------------------------


def start_timeline(
    session: Session,
    refund: Refund,
    *,
    disposition: Disposition | None,
    quality_check_passed: bool = False,
    now: datetime | None = None,
    notifier: Notifier | None = None,
) -> bool:
    """Begin the refund timeline, recording the window and notifying the customer.

    For ``WAREHOUSE_RETURN`` the timeline begins only when
    ``quality_check_passed`` is ``True`` (R17.1); for every other disposition it
    begins immediately at the confirmation event (R17.2). On start the
    ``expectedCompletionWindow`` is recorded from config and the customer is
    notified of the window (R17.8).

    Returns ``True`` when the timeline was started, ``False`` when withheld
    (warehouse without a passed quality check).
    """

    notifier = notifier or _noop_notifier
    if timeline_starts_after_quality_check(disposition) and not quality_check_passed:
        return False

    window = expected_completion_window(refund.paymentMethod)
    refund.expectedCompletionWindow = window
    refund.timelineStartedAt = now or utc_now()
    session.flush()
    notifier(
        "REFUND_TIMELINE_STARTED",
        {
            "returnRequestId": refund.returnRequestId,
            "expectedCompletionWindow": window,
            "paymentMethod": refund.paymentMethod.value,
        },
    )
    return True


# ---------------------------------------------------------------------------
# Core: issue a refund (idempotent, retrying, with PoD gate + timeline)
# ---------------------------------------------------------------------------


def issue_refund(
    session: Session,
    returnRequestId: str,
    disposition: Disposition | None,
    amountMinor: int,
    currency: str,
    paymentMethod: PaymentMethod,
    *,
    atozApplied: bool = False,
    quality_check_passed: bool = True,
    gateway: Gateway | None = None,
    notifier: Notifier | None = None,
    now: datetime | None = None,
    max_attempts: int = MAX_REFUND_ATTEMPTS,
) -> RefundOutcome:
    """Issue (at most one successful) refund for ``returnRequestId``.

    Args:
        session: Open session; the caller controls the transaction boundary.
        disposition: Triggering disposition (``None`` for an A-to-z refund).
        amountMinor: Refund amount in integer minor units, in ``currency``.
        currency: ISO-4217 order currency (R10.2).
        paymentMethod: Original order payment method; selects the timeline.
        atozApplied: ``True`` for a platform-mandated A-to-z refund (R19.4).
        quality_check_passed: For ``WAREHOUSE_RETURN`` the warehouse
            quality-check result that gates the timeline start (R17.1).
        gateway: Injectable payment gateway ``(rrid, attempt) -> bool``; defaults
            to an always-succeeds stub. Used to make retry/3-strike paths
            deterministic under test.
        notifier: Injectable customer notifier ``(event, payload)``.
        now: Injected clock for deterministic timeline timestamps.
        max_attempts: Maximum gateway attempts before flagging MANUAL (R10.4).

    Returns:
        A :class:`RefundOutcome` describing the persisted result.
    """

    gateway = gateway or _default_gateway
    notifier = notifier or _noop_notifier
    now = now or utc_now()
    notifications: list[tuple[str, dict]] = []

    def _notify(event: str, payload: dict) -> None:
        notifications.append((event, payload))
        notifier(event, payload)

    rr = session.get(ReturnRequest, returnRequestId)

    # --- Idempotency / already-issued rejection (R10.1, R10.5) ---
    existing = _get_refund(session, returnRequestId)
    if existing is not None and existing.status == RefundStatus.SUCCEEDED:
        return RefundOutcome(
            status=RefundStatus.SUCCEEDED,
            message=(
                "A refund of "
                f"{existing.amountMinor} {existing.currency} was already issued "
                f"for return request {returnRequestId}; no further refund will be "
                "made."
            ),
            refundId=existing.refundId,
            amountMinor=existing.amountMinor,
            currency=existing.currency,
            expectedCompletionWindow=existing.expectedCompletionWindow,
            timelineStarted=existing.timelineStartedAt is not None,
            atozApplied=existing.atozApplied,
            attemptCount=existing.attemptCount,
            already_issued=True,
        )

    # --- Pay-on-Delivery gate (R17.10, R18.6) ---
    if paymentMethod == PaymentMethod.PAY_ON_DELIVERY and not has_valid_bank_details(
        session, returnRequestId
    ):
        refund = _ensure_refund_row(
            session,
            returnRequestId,
            amountMinor,
            currency,
            paymentMethod,
            disposition,
            atozApplied,
            RefundStatus.WITHHELD_BANK_DETAILS,
        )
        refund.status = RefundStatus.WITHHELD_BANK_DETAILS
        refund.expectedCompletionWindow = None
        refund.timelineStartedAt = None
        if rr is not None:
            rr.status = ReturnStatus.AWAITING_BANK_DETAILS
        session.flush()
        _notify(
            "BANK_DETAILS_REQUIRED",
            {"returnRequestId": returnRequestId, "paymentMethod": paymentMethod.value},
        )
        return RefundOutcome(
            status=RefundStatus.WITHHELD_BANK_DETAILS,
            message=(
                "Bank account details are required before a Pay-on-Delivery "
                "refund can be issued; the refund timeline has not started."
            ),
            refundId=refund.refundId,
            amountMinor=amountMinor,
            currency=currency,
            atozApplied=atozApplied,
            bank_details_required=True,
            notifications=notifications,
        )

    # --- Refund row (created via repository idempotency guard) ---
    refund = _ensure_refund_row(
        session,
        returnRequestId,
        amountMinor,
        currency,
        paymentMethod,
        disposition,
        atozApplied,
        RefundStatus.PENDING,
    )
    # Keep the recorded amount/currency/disposition/flag authoritative (R10.3).
    refund.amountMinor = amountMinor
    refund.currency = currency
    refund.paymentMethod = paymentMethod
    refund.triggeringDisposition = disposition
    refund.atozApplied = atozApplied
    if rr is not None and atozApplied:
        rr.atozApplied = True

    # --- Gateway retry loop, capped at max_attempts (R10.4) ---
    succeeded = False
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        if gateway(returnRequestId, attempt):
            succeeded = True
            break

    refund.attemptCount = attempt

    if succeeded:
        refund.status = RefundStatus.SUCCEEDED
        refund.completedAt = now
        if rr is not None:
            rr.status = ReturnStatus.REFUNDED
        session.flush()
        timeline_started = start_timeline(
            session,
            refund,
            disposition=disposition,
            quality_check_passed=quality_check_passed,
            now=now,
            notifier=_notify,
        )
        return RefundOutcome(
            status=RefundStatus.SUCCEEDED,
            message=f"Refund of {amountMinor} {currency} issued successfully.",
            refundId=refund.refundId,
            amountMinor=amountMinor,
            currency=currency,
            expectedCompletionWindow=refund.expectedCompletionWindow,
            timelineStarted=timeline_started,
            atozApplied=atozApplied,
            attemptCount=attempt,
            notifications=notifications,
        )

    # --- 3 consecutive failures → flag MANUAL + notify (R10.6, R17.9) ---
    refund.status = RefundStatus.MANUAL
    if rr is not None:
        rr.status = ReturnStatus.MANUAL
    session.flush()
    _notify(
        "REFUND_FAILED_MANUAL",
        {
            "returnRequestId": returnRequestId,
            "attempts": attempt,
            "amountMinor": amountMinor,
            "currency": currency,
        },
    )
    return RefundOutcome(
        status=RefundStatus.MANUAL,
        message=(
            f"The refund could not be completed after {attempt} attempts; the "
            "return request has been flagged for manual resolution."
        ),
        refundId=refund.refundId,
        amountMinor=amountMinor,
        currency=currency,
        atozApplied=atozApplied,
        attemptCount=attempt,
        notifications=notifications,
    )


def issue_atoz_refund(
    session: Session,
    returnRequestId: str,
    purchasePriceMinor: int,
    currency: str,
    paymentMethod: PaymentMethod,
    *,
    gateway: Gateway | None = None,
    notifier: Notifier | None = None,
    now: datetime | None = None,
) -> RefundOutcome:
    """Issue an A-to-z platform refund equal to the purchase price (R19.4).

    The seller-authorization timeout trigger is wired in task 15; this helper
    provides the amount/flag path — a full refund of the recorded purchase price
    with ``atozApplied = True``.
    """

    return issue_refund(
        session,
        returnRequestId,
        disposition=None,
        amountMinor=purchasePriceMinor,
        currency=currency,
        paymentMethod=paymentMethod,
        atozApplied=True,
        quality_check_passed=True,
        gateway=gateway,
        notifier=notifier,
        now=now,
    )


#: Spec-naming alias for the A-to-z platform-refund seam (task 9.4 / R19.4).
#: The FBM seller-authorization timeout caller (task 15) invokes this.
issue_platform_refund = issue_atoz_refund


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["refund"])


def _serialize_refund(refund: Refund) -> dict:
    """Render a refund row as the GET /returns/{id}/refund response."""

    return {
        "refundId": refund.refundId,
        "returnRequestId": refund.returnRequestId,
        "amountMinor": refund.amountMinor,
        "currency": refund.currency,
        "status": refund.status.value,
        "triggeringDisposition": (
            refund.triggeringDisposition.value
            if refund.triggeringDisposition is not None
            else None
        ),
        "paymentMethod": refund.paymentMethod.value,
        "expectedCompletionWindow": refund.expectedCompletionWindow,
        "atozApplied": refund.atozApplied,
        "attemptCount": refund.attemptCount,
        "timelineStartedAt": (
            refund.timelineStartedAt.isoformat()
            if refund.timelineStartedAt is not None
            else None
        ),
    }


@router.get("/returns/{returnRequestId}/refund")
def get_refund(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Return the refund record/status for a return request (R17 surface)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "RETURN_NOT_FOUND", "message": "Return request not found."},
        )

    refund = _get_refund(session, returnRequestId)
    if refund is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "REFUND_NOT_FOUND",
                "message": "No refund has been initiated for this return request.",
            },
        )
    return _serialize_refund(refund)
