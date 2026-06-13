"""Return_Initiation_Service — the ordered initiation gate (R1, R13-R16).

This module implements the ``Return_Initiation_Service`` described in the design
("Return_Initiation_Service" section). It exposes a FastAPI :class:`APIRouter`
plus a pure-ish core function :func:`initiate_return` that the property tests
drive directly with a caller-supplied session.

Ordered initiation gate (strictly enforced — design "Initiation gate ordering"):

1. **Returnability first (R15.3, R15.4)** — reject ``NON_RETURNABLE`` before the
   window is ever consulted.
2. **Category window + allowable action (R14, R13.4)** — ``WINDOW_ELAPSED`` /
   ``ACTION_NOT_ALLOWED``.
3. **Reason + action presence (R1.2, R1.3, R13.2, R13.3)** — exactly one valid
   reason and one action in {REFUND, REPLACEMENT, EXCHANGE} (enforced at the
   HTTP boundary; ``400`` when missing/out-of-list).
4. **Category eligibility condition (R14.2-14.7, R14.10, R14.11)** —
   ``ELIGIBILITY_UNMET`` / ``VERIFICATION_REQUIRED``.
5. **Valid_Return_Condition confirmation (R16.1, R16.2)** — ``INVALID_CONDITION``
   naming each unconfirmed element.
6. **Active-return guard (R1.5)** — ``409`` if a non-terminal return already
   exists for the item.

Steps 1-2 reuse :func:`app.domain.policy.evaluate_initiation_eligibility` so the
policy is never re-derived here. A shipping-label guard
(:func:`can_generate_label`) enforces R1.4 (no label before a disposition or a
Keep It acceptance exists); the real label endpoint arrives in a later task.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import policy
from app.domain.models import (
    DAMAGED_REASONS,
    DispositionRecord,
    DoaStatus,
    FlowStep,
    Item,
    ItemCategory,
    Order,
    ReturnAction,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
    SellerType,
)
from app.domain.money import to_iso8601, utc_now
from app.domain.repository import get_session_factory

__all__ = [
    "router",
    "get_db",
    "InitiationData",
    "InitiationResult",
    "initiate_return",
    "can_generate_label",
    "SellerAuthResult",
    "authorize_seller_return",
    "apply_seller_auth_timeout",
    "VALID_CONDITION_ELEMENTS",
    "ACTIVE_STATUSES",
    "SELLER_AUTH_WINDOW_MIN_HOURS",
    "SELLER_AUTH_WINDOW_MAX_HOURS",
    "DoaVerificationRequest",
    "record_doa_verification",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The five Valid_Return_Condition elements that must all be confirmed (R16.1).
VALID_CONDITION_ELEMENTS: tuple[str, ...] = (
    "packaging",
    "tags",
    "warrantyCard",
    "manuals",
    "accessories",
)

#: Terminal statuses. A return in any *other* status counts as "active" (R1.5).
_TERMINAL_STATUSES: frozenset[ReturnStatus] = frozenset(
    {ReturnStatus.CLOSED, ReturnStatus.REJECTED}
)

#: The non-terminal statuses that block a second return for the same item.
ACTIVE_STATUSES: frozenset[ReturnStatus] = frozenset(
    s for s in ReturnStatus if s not in _TERMINAL_STATUSES
)

#: Categories/attributes that require DOA verification before approval (R16.3).
_DOA_CATEGORIES: frozenset[ItemCategory] = frozenset(
    {ItemCategory.ELECTRONICS, ItemCategory.MOBILES_LAPTOPS_ELECTRONICS}
)

#: FBM seller authorization window bounds, in hours (R19.2). The window opens at
#: submission; the seller has between :data:`SELLER_AUTH_WINDOW_MIN_HOURS` and
#: :data:`SELLER_AUTH_WINDOW_MAX_HOURS` to authorize. The recorded
#: ``sellerAuthDeadline`` uses the maximum bound — the latest instant by which a
#: seller authorization is still "within the window" (R19.3); past it the
#: A-to-z Guarantee applies (R19.4).
SELLER_AUTH_WINDOW_MIN_HOURS: int = 24
SELLER_AUTH_WINDOW_MAX_HOURS: int = 48


def _new_id(prefix: str) -> str:
    """Return a short unique id with the given prefix."""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Core data carriers (used by both the router and the property tests)
# ---------------------------------------------------------------------------


@dataclass
class InitiationData:
    """Validated inputs for :func:`initiate_return` (reason/action already typed)."""

    orderId: str
    itemId: str
    customerId: str
    reason: ReturnReason
    returnAction: ReturnAction
    validConditionConfirmed: dict
    damageProofProvided: bool = False


@dataclass
class InitiationResult:
    """Outcome of the ordered initiation gate.

    ``created`` is True only when every gate passed and a row was inserted.
    ``status_code`` is the HTTP status the router should emit (201 on success).
    ``error_code`` mirrors the design's machine-readable rejection codes.
    """

    created: bool
    status_code: int
    return_request: ReturnRequest | None = None
    error_code: str | None = None
    message: str | None = None
    allowable_actions: list[str] | None = None
    unconfirmed: list[str] | None = None


# ---------------------------------------------------------------------------
# Category eligibility condition (step 4)
# ---------------------------------------------------------------------------


def _check_category_eligibility(
    category: ItemCategory, reason: ReturnReason, damage_proof_provided: bool
) -> tuple[bool, str | None, str | None]:
    """Enforce the per-category eligibility-condition token (R14.2-14.7, R14.11).

    Returns ``(ok, error_code, message)``. Conditions handled by the
    Valid_Return_Condition confirmation (step 5) — unworn/unwashed/tags and
    unused/undamaged — pass here and are validated downstream.
    """

    token = policy.eligibility_condition(category)

    if token == "DEFECTIVE_OR_DAMAGED":
        if reason not in DAMAGED_REASONS:
            return (
                False,
                "ELIGIBILITY_UNMET",
                "This category permits a return only when the item is defective "
                "or damaged.",
            )
        return (True, None, None)

    if token == "DAMAGE_REQUIRES_VIDEO_OR_TECHNICIAN":
        if reason not in DAMAGED_REASONS:
            return (
                False,
                "ELIGIBILITY_UNMET",
                "This appliance category permits a return only for a defective or "
                "damaged item.",
            )
        if policy.requires_damage_proof(category) and not damage_proof_provided:
            return (
                False,
                "VERIFICATION_REQUIRED",
                "An unboxing video or technician verification is required for an "
                "appliance damage claim.",
            )
        return (True, None, None)

    # UNWORN_UNWASHED_TAGS / UNUSED_UNDAMAGED are confirmed via the
    # Valid_Return_Condition step; any remaining tokens belong to non-returnable
    # categories that never reach this point.
    return (True, None, None)


# ---------------------------------------------------------------------------
# Core gate (pure with respect to the supplied session)
# ---------------------------------------------------------------------------


def initiate_return(
    session: Session,
    data: InitiationData,
    now: datetime | None = None,
) -> InitiationResult:
    """Apply the ordered initiation gate and create a return request on success.

    Args:
        session: An open session. The row is flushed (not committed) so callers
            control the transaction boundary.
        data: Validated initiation inputs (reason/action already typed enums).
        now: Submission instant used for window evaluation (R14.1). Defaults to
            :func:`utc_now`; injecting it keeps window tests deterministic and
            stops the demo from breaking as real time passes.

    Returns:
        An :class:`InitiationResult`. On rejection no row is created.
    """

    submission = now or utc_now()

    item = session.get(Item, data.itemId)
    if item is None:
        return InitiationResult(
            created=False, status_code=404, error_code="ITEM_NOT_FOUND",
            message=f"Item {data.itemId} not found.",
        )
    order = session.get(Order, data.orderId)
    if order is None:
        return InitiationResult(
            created=False, status_code=404, error_code="ORDER_NOT_FOUND",
            message=f"Order {data.orderId} not found.",
        )
    if item.orderId != order.orderId:
        return InitiationResult(
            created=False, status_code=400, error_code="ITEM_ORDER_MISMATCH",
            message="The item does not belong to the supplied order.",
        )

    category = item.category

    # Steps 1-2: returnability FIRST, then window + allowable action (reuse policy).
    eligibility = policy.evaluate_initiation_eligibility(
        product_classification=item.productClassification,
        category=category,
        delivery_date=order.deliveryDate,
        submission=submission,
        action=data.returnAction,
    )
    if not eligibility.eligible:
        allowable = sorted(a.value for a in eligibility.allowable_actions)
        return InitiationResult(
            created=False,
            status_code=422,
            error_code=eligibility.rejection_code,
            message=eligibility.reason,
            allowable_actions=allowable
            if eligibility.rejection_code == policy.REJECT_ACTION_NOT_ALLOWED
            else None,
        )

    # Step 3 (reason/action presence + validity) is enforced at the HTTP
    # boundary; here both are already valid, typed enum members.

    # Step 4: category eligibility condition.
    ok, code, message = _check_category_eligibility(
        category, data.reason, data.damageProofProvided
    )
    if not ok:
        return InitiationResult(
            created=False, status_code=422, error_code=code, message=message
        )

    # Step 5: Valid_Return_Condition confirmation (all elements must be true).
    confirmed = data.validConditionConfirmed or {}
    unconfirmed = [
        element
        for element in VALID_CONDITION_ELEMENTS
        if not bool(confirmed.get(element))
    ]
    if unconfirmed:
        return InitiationResult(
            created=False,
            status_code=422,
            error_code="INVALID_CONDITION",
            message=(
                "The following Valid_Return_Condition elements were not "
                "confirmed: " + ", ".join(unconfirmed) + "."
            ),
            unconfirmed=unconfirmed,
        )

    # Step 6: active-return guard (R1.5).
    existing = session.scalar(
        select(ReturnRequest).where(
            ReturnRequest.itemId == data.itemId,
            ReturnRequest.status.in_(tuple(ACTIVE_STATUSES)),
        )
    )
    if existing is not None:
        return InitiationResult(
            created=False,
            status_code=409,
            error_code="ACTIVE_RETURN_EXISTS",
            message="An active return request already exists for this item.",
        )

    # All gates passed — create the return request and snapshot the item/order.
    doa_required = (
        category in _DOA_CATEGORIES
        or item.largeApplianceFlag
        or item.brandRequiresVerification
    )

    # Seller authorization branch (R19.1, R19.2). FBA auto-authorizes within 5 s
    # and arranges return logistics, proceeding onto the photo/proof path; FBM
    # opens a 24-48h seller authorization window and parks the request in
    # AWAITING_SELLER_AUTH until the seller authorizes (R19.3) or the window
    # elapses and the A-to-z Guarantee applies (R19.4).
    if order.sellerType == SellerType.FBM:
        initial_status = ReturnStatus.AWAITING_SELLER_AUTH
        seller_auth_deadline = submission + timedelta(
            hours=SELLER_AUTH_WINDOW_MAX_HOURS
        )
    else:
        initial_status = ReturnStatus.AWAITING_PHOTOS
        seller_auth_deadline = None

    rr = ReturnRequest(
        returnRequestId=_new_id("rr"),
        orderId=order.orderId,
        itemId=item.itemId,
        customerId=data.customerId,
        reason=data.reason,
        returnAction=data.returnAction,
        # FBA auto-authorizes onto the photo path (R19.1); FBM waits for seller
        # authorization in AWAITING_SELLER_AUTH (R19.2). The DOA gate (status
        # AWAITING_DOA) is wired in task 16.
        status=initial_status,
        flowStep=FlowStep.PROOF,
        sellerAuthDeadline=seller_auth_deadline,
        validConditionConfirmed=dict(confirmed),
        doaStatus=DoaStatus.REQUIRED if doa_required else DoaStatus.NOT_REQUIRED,
        # --- Snapshot fields copied at creation (R1.6) ---
        itemCategory=item.category,
        purchasePriceMinor=item.purchasePriceMinor,
        currency=order.currency,
        weightGrams=item.weightGrams,
        paymentMethod=order.paymentMethod,
        sellerType=order.sellerType,
        returnWindowStart=order.deliveryDate,
        excludedDispositions=[],
    )
    session.add(rr)
    session.flush()
    return InitiationResult(created=True, status_code=201, return_request=rr)


# ---------------------------------------------------------------------------
# Shipping-label guard (R1.4)
# ---------------------------------------------------------------------------


def can_generate_label(session: Session, return_request: ReturnRequest) -> bool:
    """Return whether a shipping label may be generated for ``return_request``.

    A label is refused until the ``Decision_Engine`` has selected a Disposition
    (a :class:`DispositionRecord` exists) **or** the customer has accepted a
    Keep It offer (R1.4, R11.5). The real label endpoint arrives in a later
    task; this guard makes the invariant enforceable and testable now.
    """

    if return_request.status == ReturnStatus.KEEP_IT_ACCEPTED:
        return True
    disposition = session.scalar(
        select(DispositionRecord).where(
            DispositionRecord.returnRequestId == return_request.returnRequestId
        )
    )
    return disposition is not None


# ---------------------------------------------------------------------------
# FBA/FBM seller authorization + A-to-z Guarantee (R19)
# ---------------------------------------------------------------------------


@dataclass
class SellerAuthResult:
    """Outcome of a seller-authorization action (authorize / decline / timeout).

    ``status_code`` is the HTTP status the router should emit. ``authorized`` is
    True only when the seller authorized within the window and logistics were
    arranged (R19.3). ``atozApplied`` is True when the A-to-z Guarantee was
    applied (window elapsed or seller declined), in which case ``refund`` holds
    the platform-refund outcome (R19.4, R19.5). ``notifications`` lists the
    ``(event, payload)`` tuples emitted to the customer.
    """

    status_code: int
    return_request: ReturnRequest | None = None
    error_code: str | None = None
    message: str | None = None
    authorized: bool = False
    atozApplied: bool = False
    refund: object | None = None
    notifications: list[tuple[str, dict]] = field(default_factory=list)


def _arrange_logistics(rr: ReturnRequest) -> None:
    """Arrange return logistics for an authorized return (R19.1, R19.3).

    Logistics arrangement advances the request onto the photo/proof path; the
    concrete shipping-label step remains separately gated by
    :func:`can_generate_label` (R1.4). The seller-auth deadline is cleared once
    the return is authorized.
    """

    rr.status = ReturnStatus.AWAITING_PHOTOS
    rr.flowStep = FlowStep.PROOF
    rr.sellerAuthDeadline = None


def _apply_atoz_refund(
    session: Session,
    rr: ReturnRequest,
    *,
    now: datetime,
    gateway=None,
    notifier=None,
) -> object:
    """Apply the A-to-z Guarantee: platform refund = purchase price (R19.4, R19.5).

    Issues a platform-mandated refund equal to the recorded purchase price in
    the order currency via the ``Refund_Service`` (``atozApplied = true``) and
    notifies the customer. Imported lazily to avoid a circular import
    (``refund`` depends on this module's :func:`get_db`).
    """

    # Lazy import: app.services.refund imports get_db from this module.
    from app.services.refund import issue_platform_refund

    outcome = issue_platform_refund(
        session,
        rr.returnRequestId,
        purchasePriceMinor=rr.purchasePriceMinor,
        currency=rr.currency,
        paymentMethod=rr.paymentMethod,
        gateway=gateway,
        notifier=notifier,
        now=now,
    )
    # issue_platform_refund records atozApplied=true on the refund and the
    # request, and (on success) transitions the request to REFUNDED.
    rr.atozApplied = True
    session.flush()
    if notifier is not None:
        notifier(
            "ATOZ_GUARANTEE_APPLIED",
            {
                "returnRequestId": rr.returnRequestId,
                "amountMinor": rr.purchasePriceMinor,
                "currency": rr.currency,
            },
        )
    return outcome


def authorize_seller_return(
    session: Session,
    returnRequestId: str,
    authorized: bool,
    *,
    now: datetime | None = None,
    gateway=None,
    notifier=None,
) -> SellerAuthResult:
    """Record an FBM seller's authorization decision for a return (R19.3, R19.4).

    Within the 24-48h window an ``authorized = True`` decision arranges return
    logistics and advances the request to ``AWAITING_PHOTOS`` (R19.3). If the
    window has already elapsed, or the seller declines (``authorized = False``),
    the A-to-z Guarantee is applied: a platform refund equal to the recorded
    purchase price is issued, ``atozApplied`` is recorded, and the customer is
    notified (R19.4, R19.5).

    Args:
        session: Open session; the caller controls the transaction boundary.
        returnRequestId: The FBM return request awaiting authorization.
        authorized: The seller's decision.
        now: Injected clock used for the window check (defaults to
            :func:`utc_now`).
        gateway: Optional payment gateway forwarded to the Refund_Service.
        notifier: Optional customer notifier ``(event, payload)``.

    Returns:
        A :class:`SellerAuthResult`.
    """

    moment = now or utc_now()
    notifications: list[tuple[str, dict]] = []

    def _notify(event: str, payload: dict) -> None:
        notifications.append((event, payload))
        if notifier is not None:
            notifier(event, payload)

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        return SellerAuthResult(
            status_code=404,
            error_code="RETURN_NOT_FOUND",
            message="Return request not found.",
        )
    if rr.sellerType != SellerType.FBM:
        return SellerAuthResult(
            status_code=409,
            return_request=rr,
            error_code="NOT_FBM",
            message="Seller authorization applies only to FBM return requests.",
        )
    if rr.status != ReturnStatus.AWAITING_SELLER_AUTH:
        return SellerAuthResult(
            status_code=409,
            return_request=rr,
            error_code="NOT_AWAITING_SELLER_AUTH",
            message=(
                "This return request is not awaiting seller authorization "
                f"(current status: {rr.status.value})."
            ),
        )

    deadline = rr.sellerAuthDeadline
    if deadline is not None and deadline.tzinfo is None:
        from datetime import timezone
        deadline = deadline.replace(tzinfo=timezone.utc)
        
    if moment.tzinfo is None:
        from datetime import timezone
        moment = moment.replace(tzinfo=timezone.utc)

    window_elapsed = (
        deadline is not None and moment > deadline
    )

    if window_elapsed or not authorized:
        refund = _apply_atoz_refund(
            session, rr, now=moment, gateway=gateway, notifier=_notify
        )
        reason = (
            "the seller authorization window elapsed"
            if window_elapsed
            else "the seller declined the return"
        )
        return SellerAuthResult(
            status_code=200,
            return_request=rr,
            atozApplied=True,
            authorized=False,
            message=(
                f"The A-to-z Guarantee was applied because {reason}; a "
                "platform-mandated refund equal to the purchase price was issued."
            ),
            refund=refund,
            notifications=notifications,
        )

    # Authorized within the window — arrange return logistics (R19.3).
    _arrange_logistics(rr)
    session.flush()
    _notify(
        "RETURN_LOGISTICS_ARRANGED",
        {"returnRequestId": rr.returnRequestId, "sellerType": rr.sellerType.value},
    )
    return SellerAuthResult(
        status_code=200,
        return_request=rr,
        authorized=True,
        message="The seller authorized the return; return logistics were arranged.",
        notifications=notifications,
    )


def apply_seller_auth_timeout(
    session: Session,
    returnRequestId: str,
    *,
    now: datetime | None = None,
    gateway=None,
    notifier=None,
) -> SellerAuthResult:
    """Apply the A-to-z Guarantee when the FBM seller-auth window elapses (R19.4).

    This is the timeout-driven entry point the scheduler (task 21) invokes once
    a return's ``sellerAuthDeadline`` has passed without authorization. It is a
    safe no-op (``status_code = 409``) when the request is not an FBM request
    awaiting authorization, or when the window has not yet elapsed, so the
    scheduler can re-run it idempotently.
    """

    moment = now or utc_now()
    notifications: list[tuple[str, dict]] = []

    def _notify(event: str, payload: dict) -> None:
        notifications.append((event, payload))
        if notifier is not None:
            notifier(event, payload)

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        return SellerAuthResult(
            status_code=404,
            error_code="RETURN_NOT_FOUND",
            message="Return request not found.",
        )
    if rr.sellerType != SellerType.FBM or rr.status != ReturnStatus.AWAITING_SELLER_AUTH:
        return SellerAuthResult(
            status_code=409,
            return_request=rr,
            error_code="NOT_AWAITING_SELLER_AUTH",
            message="There is no pending FBM seller authorization to time out.",
        )
    if rr.sellerAuthDeadline is not None and moment < rr.sellerAuthDeadline:
        return SellerAuthResult(
            status_code=409,
            return_request=rr,
            error_code="WINDOW_ACTIVE",
            message="The seller authorization window has not yet elapsed.",
        )

    refund = _apply_atoz_refund(
        session, rr, now=moment, gateway=gateway, notifier=_notify
    )
    return SellerAuthResult(
        status_code=200,
        return_request=rr,
        atozApplied=True,
        authorized=False,
        message=(
            "The A-to-z Guarantee was applied after the seller authorization "
            "window elapsed; a platform-mandated refund equal to the purchase "
            "price was issued."
        ),
        refund=refund,
        notifications=notifications,
    )


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["return-initiation"])


def get_db() -> Session:
    """FastAPI dependency yielding a session; commits on success, rolls back on error.

    Tests override this via ``app.dependency_overrides`` to bind a disposable
    in-memory database.
    """

    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class ValidConditionConfirmed(BaseModel):
    """Confirmation flags for the five Valid_Return_Condition elements (R16.1)."""

    packaging: bool = False
    tags: bool = False
    warrantyCard: bool = False
    manuals: bool = False
    accessories: bool = False


class InitiationRequest(BaseModel):
    """POST /returns request body.

    ``reason`` and ``returnAction`` are accepted as raw strings (not typed
    enums) so the service can return a precise ``400`` for a missing or
    out-of-list selection rather than a generic validation error (R1.3, R13.3).
    ``submittedAt`` is an optional now-injection seam for window evaluation.
    """

    orderId: str
    itemId: str
    customerId: str
    reason: str | None = None
    returnAction: str | None = None
    validConditionConfirmed: ValidConditionConfirmed = Field(
        default_factory=ValidConditionConfirmed
    )
    damageProofProvided: bool = False
    submittedAt: str | None = None


def _parse_submitted_at(value: str | None) -> datetime | None:
    """Parse an optional ISO date/datetime ``submittedAt`` into a datetime."""

    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 10:  # YYYY-MM-DD -> noon avoids window edge ambiguity
            d = date.fromisoformat(text)
            return datetime(d.year, d.month, d.day, 12, 0, 0)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=400, detail={"error": "INVALID_SUBMITTED_AT", "message": str(exc)}
        )


def _serialize(rr: ReturnRequest) -> dict:
    """Render a ReturnRequest as the design's POST /returns 201 response."""

    return {
        "returnRequestId": rr.returnRequestId,
        "status": rr.status.value,
        "itemCategory": rr.itemCategory.value,
        "returnAction": rr.returnAction.value,
        "purchasePrice": rr.purchasePriceMinor,
        "currency": rr.currency,
        "paymentMethod": rr.paymentMethod.value,
        "sellerType": rr.sellerType.value,
        "returnWindowStart": rr.returnWindowStart.isoformat(),
        "createdAt": to_iso8601(rr.createdAt),
    }


@router.post("/returns", status_code=201)
def post_returns(body: InitiationRequest, session: Session = Depends(get_db)) -> dict:
    """Initiate a return through the ordered initiation gate."""

    # Step 3 (presence + validity), enforced before the core gate maps it back
    # into the strict ordering: returnability/window are still evaluated first
    # because reason/action validity here only guards malformed *requests*.
    if body.reason is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "REASON_REQUIRED", "message": "A return reason is required."},
        )
    try:
        reason = ReturnReason(body.reason)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_REASON",
                "message": f"'{body.reason}' is not a valid return reason.",
            },
        )

    if body.returnAction is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "ACTION_REQUIRED",
                "message": "Exactly one return action is required.",
            },
        )
    try:
        action = ReturnAction(body.returnAction)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_ACTION",
                "message": f"'{body.returnAction}' is not a valid return action.",
            },
        )

    data = InitiationData(
        orderId=body.orderId,
        itemId=body.itemId,
        customerId=body.customerId,
        reason=reason,
        returnAction=action,
        validConditionConfirmed=body.validConditionConfirmed.model_dump(),
        damageProofProvided=body.damageProofProvided,
    )
    now = _parse_submitted_at(body.submittedAt)
    result = initiate_return(session, data, now=now)

    if result.created and result.return_request is not None:
        return _serialize(result.return_request)

    detail: dict = {"error": result.error_code, "message": result.message}
    if result.allowable_actions is not None:
        detail["allowableActions"] = result.allowable_actions
    if result.unconfirmed is not None:
        detail["unconfirmed"] = result.unconfirmed
    raise HTTPException(status_code=result.status_code, detail=detail)


@router.get("/returns/{returnRequestId}")
def get_return(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Return the current state of a return request (score/disposition arrive later)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "RETURN_NOT_FOUND", "message": "Return request not found."},
        )

    disposition = session.scalar(
        select(DispositionRecord).where(
            DispositionRecord.returnRequestId == returnRequestId
        )
    )
    payload = _serialize(rr)
    payload.update(
        {
            "orderId": rr.orderId,
            "itemId": rr.itemId,
            "customerId": rr.customerId,
            "reason": rr.reason.value,
            "flowStep": rr.flowStep.value,
            "doaStatus": rr.doaStatus.value,
            "validConditionConfirmed": rr.validConditionConfirmed,
            "sellerAuthDeadline": (
                rr.sellerAuthDeadline.isoformat()
                if rr.sellerAuthDeadline is not None
                else None
            ),
            "atozApplied": rr.atozApplied,
            # Populated by later tasks; null until then.
            "secondLifeScore": None,
            "disposition": disposition.selected.value if disposition else None,
            "canGenerateLabel": can_generate_label(session, rr),
        }
    )
    return payload


class SellerAuthRequest(BaseModel):
    """POST /returns/{id}/seller-auth request body (R19.3).

    ``authorized`` is the FBM seller's decision. ``actedAt`` is an optional
    now-injection seam used for the seller-auth window check so the
    within-window / elapsed paths are deterministic under test.
    """

    authorized: bool = True
    actedAt: str | None = None


def _serialize_seller_auth(result: SellerAuthResult) -> dict:
    """Render a seller-authorization outcome as the POST /seller-auth response."""

    rr = result.return_request
    payload: dict = {
        "returnRequestId": rr.returnRequestId if rr is not None else None,
        "status": rr.status.value if rr is not None else None,
        "authorized": result.authorized,
        "atozApplied": result.atozApplied,
        "message": result.message,
    }
    refund = result.refund
    if refund is not None:
        status = getattr(refund, "status", None)
        payload["refundStatus"] = status.value if status is not None else None
        payload["refundAmount"] = getattr(refund, "amountMinor", None)
        payload["currency"] = getattr(refund, "currency", None)
    return payload


@router.post("/returns/{returnRequestId}/seller-auth")
def post_seller_auth(
    returnRequestId: str,
    body: SellerAuthRequest,
    session: Session = Depends(get_db),
) -> dict:
    """Record an FBM seller's authorization decision within the window (R19.3, R19.4).

    ``{ "authorized": true }`` within the 24-48h window arranges return logistics
    and advances the request to ``AWAITING_PHOTOS`` (R19.3). If the window has
    elapsed or the seller declines, the A-to-z Guarantee is applied: a
    platform-mandated refund equal to the recorded purchase price is issued,
    ``atozApplied`` is recorded, and the customer is notified (R19.4, R19.5).
    """

    now = _parse_submitted_at(body.actedAt)
    result = authorize_seller_return(
        session, returnRequestId, body.authorized, now=now
    )
    if result.error_code is not None:
        raise HTTPException(
            status_code=result.status_code,
            detail={"error": result.error_code, "message": result.message},
        )
    return _serialize_seller_auth(result)


class DoaVerificationRequest(BaseModel):
    """POST /returns/{id}/doa request body (R16.4)."""

    source: str
    confirmsDoa: bool


def record_doa_verification(
    session: Session,
    returnRequestId: str,
    source: str,
    confirmsDoa: bool,
) -> dict:
    """Record a DOA verification outcome (R16.4, R16.5)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "RETURN_NOT_FOUND", "message": "Return request not found."},
        )
    if rr.doaStatus == DoaStatus.NOT_REQUIRED:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "DOA_NOT_REQUIRED",
                "message": "DOA verification is not required for this item.",
            },
        )

    if source not in ("CERTIFICATE", "TECHNICIAN"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_DOA_SOURCE",
                "message": "DOA source must be CERTIFICATE or TECHNICIAN.",
            },
        )

    if confirmsDoa:
        rr.doaStatus = DoaStatus.SATISFIED
        if rr.status == ReturnStatus.AWAITING_DOA:
            rr.status = ReturnStatus.SCORED
        session.flush()
        return {
            "returnRequestId": rr.returnRequestId,
            "doaStatus": rr.doaStatus.value,
            "status": rr.status.value,
            "message": "DOA verification satisfied.",
        }
    else:
        rr.doaStatus = DoaStatus.FAILED
        rr.status = ReturnStatus.MANUAL
        session.flush()
        return {
            "returnRequestId": rr.returnRequestId,
            "doaStatus": rr.doaStatus.value,
            "status": rr.status.value,
            "message": "The item did not pass DOA_Verification.",
        }


@router.post("/returns/{returnRequestId}/doa")
def post_doa(
    returnRequestId: str,
    body: DoaVerificationRequest,
    session: Session = Depends(get_db),
) -> dict:
    """Record a DOA verification outcome (R16.4, R16.5)."""

    return record_doa_verification(
        session, returnRequestId, body.source, body.confirmsDoa
    )


@router.get("/return-reasons")

def get_return_reasons() -> dict:
    """Return the defined list of valid return reasons (R1.2; includes Keep It)."""

    return {"reasons": [r.value for r in ReturnReason]}


@router.get("/categories/{category}/policy")
def get_category_policy(category: str) -> dict:
    """Return the CategoryPolicy view used by the initiation gate (R14)."""

    try:
        cat = ItemCategory(category)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "UNKNOWN_CATEGORY",
                "message": f"'{category}' is not a known item category.",
            },
        )

    view = policy.get_policy(cat)
    return {
        "category": cat.value,
        "resolvedCategory": view.category.value,
        "displayName": view.display_name,
        "windowDays": view.window_days,
        "allowableActions": sorted(a.value for a in view.allowable_actions),
        "eligibilityCondition": view.eligibility_condition,
        "returnable": view.returnable,
        "requiresDamageProof": view.requires_damage_proof,
    }
