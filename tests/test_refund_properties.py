"""Property-based tests for the Refund_Service (task 9).

Covers design Correctness Properties 20, 21, 41, and 42 for
``app.services.refund``. Each test is tagged with the exact
``Feature: secondlife-ai, Property {n}: {text}`` comment and a
``Validates: Requirements ...`` line, and runs against the Hypothesis ``ci``
profile (>= 100 examples; see ``tests/conftest.py``).

Each example builds a fresh in-memory SQLite database (tables + standard
fixtures), creates a return request with a chosen ``Payment_Method`` snapshot,
and drives issuance deterministically through the injectable gateway seam
``gateway(returnRequestId, attempt) -> bool`` — no mocks, no network.
"""

from __future__ import annotations

import uuid

from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import (
    BankDetails,
    Base,
    Disposition,
    Item,
    Order,
    PaymentMethod,
    Refund,
    RefundStatus,
    ReturnAction,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)
from app.fixtures.loader import load_all
from app.services.refund import (
    PAYMENT_METHOD_WINDOWS,
    expected_completion_window,
    has_valid_bank_details,
    issue_refund,
    timeline_starts_after_quality_check,
)

# Normal (non-Keep It) dispositions that trigger a full-price refund.
_NORMAL_DISPOSITIONS = [
    Disposition.WAREHOUSE_RETURN,
    Disposition.HYPERLOCAL_RESALE,
    Disposition.GREEN_DONATION,
]
_NON_POD_METHODS = [m for m in PaymentMethod if m != PaymentMethod.PAY_ON_DELIVERY]


def _make_seeded_factory():
    """Create a disposable in-memory engine with tables + standard fixtures."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = factory()
    try:
        load_all(session)
        session.commit()
    finally:
        session.close()
    return engine, factory


def _new_rr(
    session,
    *,
    payment_method: PaymentMethod,
    price: int,
    currency: str = "INR",
    status: ReturnStatus = ReturnStatus.DECIDED,
) -> ReturnRequest:
    """Create a return request with a chosen Payment_Method / price snapshot.

    References a seeded order/item/customer for FK shape; the refund call is
    given the order currency and payment method explicitly.
    """

    item = session.get(Item, "item_elec_01")
    order = session.get(Order, "ord_1001")
    rr = ReturnRequest(
        returnRequestId=f"rr_{uuid.uuid4().hex[:10]}",
        orderId=order.orderId,
        itemId=item.itemId,
        customerId=order.customerId,
        reason=ReturnReason.DEFECTIVE,
        returnAction=ReturnAction.REPLACEMENT,
        status=status,
        itemCategory=item.category,
        purchasePriceMinor=price,
        currency=currency,
        weightGrams=item.weightGrams,
        paymentMethod=payment_method,
        sellerType=order.sellerType,
        returnWindowStart=order.deliveryDate,
        excludedDispositions=[],
    )
    session.add(rr)
    session.flush()
    return rr


def _accept_bank_details(session, return_request_id: str) -> None:
    """Insert an accepted (valid) bank-details row for a return request."""

    session.add(
        BankDetails(
            bankDetailsId=f"bd_{uuid.uuid4().hex[:10]}",
            returnRequestId=return_request_id,
            ifscEncrypted=b"enc-ifsc",
            accountNumberEncrypted=b"enc-acct",
            accepted=True,
        )
    )
    session.flush()


def _refund_row(session, return_request_id: str) -> Refund | None:
    return session.scalar(
        select(Refund).where(Refund.returnRequestId == return_request_id)
    )


# ===========================================================================
# Property 20 — refund correctness
# ===========================================================================
# Feature: secondlife-ai, Property 20: Refund correctness
# Validates: Requirements 4.4, 5.5, 7.4, 10.2, 10.3
@given(
    disposition=st.sampled_from(_NORMAL_DISPOSITIONS),
    payment_method=st.sampled_from(_NON_POD_METHODS),
    price=st.integers(min_value=1, max_value=5_000_000),
    currency=st.sampled_from(["INR", "USD", "EUR"]),
)
def test_property_20_refund_correctness(
    disposition, payment_method, price, currency
) -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(
                session, payment_method=payment_method, price=price, currency=currency
            )
            outcome = issue_refund(
                session,
                rr.returnRequestId,
                disposition,
                price,
                currency,
                payment_method,
            )

            assert outcome.status == RefundStatus.SUCCEEDED
            refund = _refund_row(session, rr.returnRequestId)
            # Amount equals the recorded purchase price, in the order currency.
            assert refund.amountMinor == price == rr.purchasePriceMinor
            assert refund.currency == currency == rr.currency
            # References the return request and the triggering disposition.
            assert refund.returnRequestId == rr.returnRequestId
            assert refund.triggeringDisposition == disposition
            assert refund.status == RefundStatus.SUCCEEDED
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 21 — at most one successful refund per return request
# ===========================================================================
# Feature: secondlife-ai, Property 21: At most one successful refund per return request
# Validates: Requirements 10.1, 10.4, 10.5
@given(
    # A sequence of gateway behaviors; each entry is the number of leading
    # failed attempts that issuance call simulates before (possibly) succeeding.
    fail_counts=st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=6),
    price=st.integers(min_value=1, max_value=2_000_000),
)
def test_property_21_at_most_one_successful_refund(fail_counts, price) -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(session, payment_method=PaymentMethod.UPI, price=price)

            success_count = 0
            for fails in fail_counts:
                before = _refund_row(session, rr.returnRequestId)
                already_succeeded = (
                    before is not None and before.status == RefundStatus.SUCCEEDED
                )
                prior_amount = before.amountMinor if before is not None else None

                # Gateway fails the first ``fails`` attempts of THIS call.
                def gateway(_rrid: str, attempt: int, _fails=fails) -> bool:
                    return attempt > _fails

                outcome = issue_refund(
                    session,
                    rr.returnRequestId,
                    Disposition.WAREHOUSE_RETURN,
                    price,
                    "INR",
                    PaymentMethod.UPI,
                    gateway=gateway,
                )

                after = _refund_row(session, rr.returnRequestId)

                if already_succeeded:
                    # R10.5: once succeeded, every subsequent request is rejected,
                    # and the previously refunded amount is left unchanged.
                    assert outcome.already_issued is True
                    assert outcome.status == RefundStatus.SUCCEEDED
                    assert after.status == RefundStatus.SUCCEEDED
                    assert after.amountMinor == prior_amount
                elif outcome.status == RefundStatus.SUCCEEDED:
                    success_count += 1

                # R10.4: failing attempts are capped at 3 per issuance.
                assert after.attemptCount <= 3

            # R10.1: at most one successful refund across the whole sequence.
            assert success_count <= 1
            final = _refund_row(session, rr.returnRequestId)
            assert final.attemptCount <= 3
            # Exactly one refund row exists for the return request (R10.1).
            rows = list(
                session.scalars(
                    select(Refund).where(
                        Refund.returnRequestId == rr.returnRequestId
                    )
                )
            )
            assert len(rows) == 1
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 41 — refund timeline selection by payment method
# ===========================================================================
# Feature: secondlife-ai, Property 41: Refund timeline selection by payment method
# Validates: Requirements 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8
@given(
    payment_method=st.sampled_from(list(PaymentMethod)),
    disposition=st.sampled_from(_NORMAL_DISPOSITIONS),
    price=st.integers(min_value=1, max_value=5_000_000),
    quality_check_passed=st.booleans(),
)
def test_property_41_timeline_selection(
    payment_method, disposition, price, quality_check_passed
) -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(session, payment_method=payment_method, price=price)
            # For Pay-on-Delivery the timeline only starts once bank details are
            # captured (the gate is asserted by Property 42); capture them here so
            # this property isolates window selection + start event.
            if payment_method == PaymentMethod.PAY_ON_DELIVERY:
                _accept_bank_details(session, rr.returnRequestId)

            outcome = issue_refund(
                session,
                rr.returnRequestId,
                disposition,
                price,
                "INR",
                payment_method,
                quality_check_passed=quality_check_passed,
            )
            assert outcome.status == RefundStatus.SUCCEEDED

            refund = _refund_row(session, rr.returnRequestId)

            # The start event matches the disposition (R17.1 warehouse quality
            # check; R17.2 confirmation event for Keep It / resale / donation).
            warehouse_gated = (
                timeline_starts_after_quality_check(disposition)
                and not quality_check_passed
            )
            if warehouse_gated:
                # Timeline withheld until the warehouse quality check passes.
                assert outcome.timelineStarted is False
                assert refund.timelineStartedAt is None
                assert refund.expectedCompletionWindow is None
            else:
                # Timeline started: recorded window equals the configured window
                # for the payment method (R17.3-17.7) and the customer is notified
                # of that window (R17.8).
                assert outcome.timelineStarted is True
                assert refund.timelineStartedAt is not None
                assert (
                    refund.expectedCompletionWindow
                    == PAYMENT_METHOD_WINDOWS[payment_method]
                )
                assert (
                    outcome.expectedCompletionWindow
                    == PAYMENT_METHOD_WINDOWS[payment_method]
                )
                started = [
                    e for e, _ in outcome.notifications
                    if e == "REFUND_TIMELINE_STARTED"
                ]
                assert started, "customer must be notified of the expected window"

            # The selector matches the config for every payment method (R17.3-17.7).
            assert (
                expected_completion_window(payment_method)
                == PAYMENT_METHOD_WINDOWS[payment_method]
            )
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 42 — Pay-on-Delivery refund withheld until valid bank details
# ===========================================================================
# Feature: secondlife-ai, Property 42: Pay-on-Delivery refund withheld until valid bank details
# Validates: Requirements 17.10, 18.6
@given(
    disposition=st.sampled_from(_NORMAL_DISPOSITIONS),
    price=st.integers(min_value=1, max_value=5_000_000),
    capture_then_retry=st.booleans(),
)
def test_property_42_pod_withheld_until_bank_details(
    disposition, price, capture_then_retry
) -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(
                session, payment_method=PaymentMethod.PAY_ON_DELIVERY, price=price
            )
            assert has_valid_bank_details(session, rr.returnRequestId) is False

            outcome = issue_refund(
                session,
                rr.returnRequestId,
                disposition,
                price,
                "INR",
                PaymentMethod.PAY_ON_DELIVERY,
            )

            # Refund + timeline withheld; bank-details-required message returned.
            assert outcome.status == RefundStatus.WITHHELD_BANK_DETAILS
            assert outcome.bank_details_required is True
            assert "bank" in (outcome.message or "").lower()

            refund = _refund_row(session, rr.returnRequestId)
            assert refund.status == RefundStatus.WITHHELD_BANK_DETAILS
            assert refund.timelineStartedAt is None
            assert refund.expectedCompletionWindow is None
            assert refund.attemptCount == 0  # withholding is not a failed attempt
            session.refresh(rr)
            assert rr.status == ReturnStatus.AWAITING_BANK_DETAILS

            if capture_then_retry:
                # Once valid bank details are captured, the refund proceeds.
                _accept_bank_details(session, rr.returnRequestId)
                retry = issue_refund(
                    session,
                    rr.returnRequestId,
                    disposition,
                    price,
                    "INR",
                    PaymentMethod.PAY_ON_DELIVERY,
                )
                assert retry.status == RefundStatus.SUCCEEDED
                refund2 = _refund_row(session, rr.returnRequestId)
                assert refund2.status == RefundStatus.SUCCEEDED
                assert refund2.timelineStartedAt is not None
                assert (
                    refund2.expectedCompletionWindow
                    == PAYMENT_METHOD_WINDOWS[PaymentMethod.PAY_ON_DELIVERY]
                )
        finally:
            session.close()
    finally:
        engine.dispose()
