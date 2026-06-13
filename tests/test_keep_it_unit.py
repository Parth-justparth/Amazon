"""Minimal unit tests for the Keep_It_Service (task 13.1 implementation).

These are example-based smoke/behaviour tests verifying the module imports and
that the core math, eligibility, and offer lifecycle behave correctly. The
numbered property-based tests (Properties 29-32) live in the optional tasks
13.2-13.5 and are intentionally not implemented here.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import (
    Base,
    ConditionAssessment,
    Disposition,
    Item,
    KeepItOffer,
    KeepItOfferState,
    Order,
    Refund,
    ReturnAction,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)
from app.fixtures.loader import load_all
from app.fixtures.seed_data import GLOBAL_CONFIG
from app.services import keep_it


# ---------------------------------------------------------------------------
# Pure bounds math (R11.2, R11.3)
# ---------------------------------------------------------------------------


def test_compute_partial_refund_amount_worked_example():
    """Design worked example: P=38990, RLC=25250, DIV=9000 -> 11697."""

    amount = keep_it.compute_partial_refund_amount(38_990, 25_250, 9_000)
    assert amount == 11_697
    # All four bounds hold simultaneously (R11.2, R11.3).
    assert amount > 0
    assert amount < 38_990  # < P
    assert amount < 25_250  # < RLC
    assert amount + 9_000 <= 25_250  # net-profit cap


def test_compute_partial_refund_amount_clamps_to_upper():
    """A large raw factor clamps down to the net-profit cap."""

    # cap = RLC - DIV = 16250 is the binding upper bound.
    amount = keep_it.compute_partial_refund_amount(
        38_990, 25_250, 9_000, config={**GLOBAL_CONFIG, "keep_it_refund_factor": 0.99}
    )
    assert amount == 16_250
    assert amount + 9_000 <= 25_250


def test_compute_partial_refund_amount_none_when_no_valid_amount():
    """When RLC <= DIV no positive bounded amount exists -> None (skip Keep It)."""

    assert keep_it.compute_partial_refund_amount(38_990, 5_000, 9_000) is None
    # RLC == DIV -> cap == 0 -> upper < 1 -> None.
    assert keep_it.compute_partial_refund_amount(38_990, 9_000, 9_000) is None


# ---------------------------------------------------------------------------
# Eligibility (R11.1)
# ---------------------------------------------------------------------------


def test_is_keep_it_eligible_trigger_conditions():
    # Minor issue + score at threshold -> eligible.
    assert keep_it.is_keep_it_eligible(ReturnReason.MINOR_DEFECT, 70) is True
    assert (
        keep_it.is_keep_it_eligible(ReturnReason.COLOR_APPEARANCE_NOT_AS_EXPECTED, 95)
        is True
    )
    # Below threshold -> not eligible.
    assert keep_it.is_keep_it_eligible(ReturnReason.MINOR_DEFECT, 69) is False
    # Non-minor reason -> not eligible regardless of score.
    assert keep_it.is_keep_it_eligible(ReturnReason.DEFECTIVE, 100) is False
    # Missing score -> not eligible.
    assert keep_it.is_keep_it_eligible(ReturnReason.MINOR_DEFECT, None) is False


# ---------------------------------------------------------------------------
# Lifecycle (evaluate / accept / decline) against a seeded in-memory DB
# ---------------------------------------------------------------------------


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = session_factory()
    try:
        load_all(session)
        session.commit()
    finally:
        session.close()
    yield session_factory
    engine.dispose()


def _make_rr(
    session, *, reason=ReturnReason.MINOR_DEFECT, item_id="item_keepit_01", score=85
):
    item = session.get(Item, item_id)
    order = session.get(Order, item.orderId)
    rr = ReturnRequest(
        returnRequestId=f"rr_{uuid.uuid4().hex[:10]}",
        orderId=order.orderId,
        itemId=item.itemId,
        customerId=order.customerId,
        reason=reason,
        returnAction=ReturnAction.REFUND,
        status=ReturnStatus.SCORED,
        itemCategory=item.category,
        purchasePriceMinor=item.purchasePriceMinor,
        currency=item.currency,
        weightGrams=item.weightGrams,
        paymentMethod=order.paymentMethod,
        sellerType=order.sellerType,
        returnWindowStart=order.deliveryDate,
        excludedDispositions=[],
    )
    session.add(rr)
    session.flush()
    # Record the SecondLife_Score (the Decision_Engine reads it from here on the
    # decline/expiry routing path).
    session.add(
        ConditionAssessment(
            assessmentId=f"ca_{uuid.uuid4().hex[:10]}",
            returnRequestId=rr.returnRequestId,
            secondLifeScore=score,
            conditionSummary="Minor cosmetic dent.",
            photoCount=1,
        )
    )
    session.flush()
    return rr


def test_evaluate_presents_offer_for_eligible_minor_issue(factory):
    session = factory()
    try:
        rr = _make_rr(session)
        result = keep_it.evaluate_keep_it(session, rr, score=85)
        assert result.presented is True
        assert result.offer is not None
        assert result.offer.state == KeepItOfferState.PRESENTED
        assert result.offer.partialRefundAmountMinor > 0
        assert result.offer.expiresAt is not None
        assert rr.status == ReturnStatus.KEEP_IT_OFFERED
    finally:
        session.close()


def test_evaluate_skips_and_routes_when_not_eligible(factory):
    session = factory()
    try:
        rr = _make_rr(session, reason=ReturnReason.DEFECTIVE)
        result = keep_it.evaluate_keep_it(session, rr, score=85)
        assert result.presented is False
        assert result.decision is not None and result.decision.ok is True
        # KEEP_IT excluded from the routed disposition.
        assert "KEEP_IT" in (rr.excludedDispositions or [])
        assert result.decision.final != Disposition.KEEP_IT
    finally:
        session.close()


def test_accept_issues_one_refund_and_one_credit(factory):
    session = factory()
    try:
        rr = _make_rr(session)
        keep_it.evaluate_keep_it(session, rr, score=85)
        outcome = keep_it.accept_offer(session, rr)

        assert outcome.offer.state == KeepItOfferState.ACCEPTED
        assert rr.status == ReturnStatus.KEEP_IT_ACCEPTED
        assert outcome.refund.amountMinor == outcome.offer.partialRefundAmountMinor
        assert outcome.points_credited == GLOBAL_CONFIG["green_points_keep_it"]

        # Exactly one refund row for the return request (R11.9, R10.1).
        refunds = session.scalars(
            select(Refund).where(Refund.returnRequestId == rr.returnRequestId)
        ).all()
        assert len(refunds) == 1
        assert refunds[0].triggeringDisposition == Disposition.KEEP_IT
    finally:
        session.close()


def test_decline_routes_to_decision_excluding_keep_it(factory):
    session = factory()
    try:
        rr = _make_rr(session)
        keep_it.evaluate_keep_it(session, rr, score=85)
        outcome = keep_it.decline_offer(session, rr)

        offer = session.scalar(
            select(KeepItOffer).where(KeepItOffer.returnRequestId == rr.returnRequestId)
        )
        assert offer.state == KeepItOfferState.DECLINED
        assert outcome.ok is True
        assert outcome.final != Disposition.KEEP_IT
        assert "KEEP_IT" in (rr.excludedDispositions or [])
    finally:
        session.close()
