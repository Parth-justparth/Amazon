"""Property-based tests for the Keep_It_Service (task 13.2).

Covers design Correctness Property 29 for ``app.services.keep_it``. The test is
tagged with the exact ``Feature: secondlife-ai, Property 29: ...`` comment and a
``Validates: Requirements 11.1`` line, and runs against the Hypothesis ``ci``
profile (>= 100 examples; see ``tests/conftest.py``) with ``STUB_MODE`` enabled
for the Decision_Engine skip path.

Property 29 (Keep It offer trigger conditions): for any return request a
``Keep_It_Offer`` is presented **if and only if** the recorded reason is a
``Minor_Issue_Reason`` AND the ``SecondLife_Score`` is ``>= keepItMinScore`` AND
a positive ``Partial_Refund_Amount`` satisfying all bounds exists; otherwise no
offer is presented and the request routes to the Decision_Engine.
"""

from __future__ import annotations

import uuid

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import (
    Base,
    Disposition,
    Item,
    KeepItOffer,
    KeepItOfferState,
    MINOR_ISSUE_REASONS,
    Order,
    ReturnAction,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)
from app.fixtures.loader import load_all
from app.fixtures.seed_data import GLOBAL_CONFIG
from app.integrations.openai_client import (
    OpenAIVisionClient,
    StubDecisionConfig,
    StubDecisionMode,
)
from app.services import keep_it
from app.services.decision_engine import compute_economics

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_REASONS = list(ReturnReason)
# Demo items wired to real orders (FK targets); category/price/weight are
# overridden per example so the economics (RLC/DIV) span the input space.
_DEMO = {
    "item_elec_01": "ord_1001",
    "item_appl_01": "ord_1002",
    "item_foot_01": "ord_1003",
}


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


def _new_rr(session, *, item_id, order_id, reason, price, weight) -> ReturnRequest:
    """Create a SCORED return request with overridden economics drivers."""

    item = session.get(Item, item_id)
    order = session.get(Order, order_id)
    rr = ReturnRequest(
        returnRequestId=f"rr_{uuid.uuid4().hex[:10]}",
        orderId=order_id,
        itemId=item_id,
        customerId=order.customerId,
        reason=reason,
        returnAction=ReturnAction.REFUND,
        status=ReturnStatus.SCORED,
        itemCategory=item.category,
        purchasePriceMinor=price,
        currency=order.currency,
        weightGrams=weight,
        paymentMethod=order.paymentMethod,
        sellerType=order.sellerType,
        returnWindowStart=order.deliveryDate,
        excludedDispositions=[],
    )
    session.add(rr)
    from app.domain.models import ConditionAssessment
    rr.conditionAssessment = ConditionAssessment(
        assessmentId=f"ca_{uuid.uuid4().hex[:10]}",
        returnRequestId=rr.returnRequestId,
        secondLifeScore=90,
        conditionSummary="Pristine condition",
        photoCount=1
    )
    session.add(rr.conditionAssessment)
    session.flush()
    return rr


def _client() -> OpenAIVisionClient:
    """STUB_MODE vision client driving a VALID decision on the skip path."""

    return OpenAIVisionClient(
        decision_config=StubDecisionConfig(mode=StubDecisionMode.VALID)
    )


# ===========================================================================
# Property 29 — Keep It offer trigger conditions
# ===========================================================================
# Feature: secondlife-ai, Property 29: Keep It offer trigger conditions
# Validates: Requirements 11.1
@given(
    reason=st.sampled_from(_ALL_REASONS),
    score=st.integers(min_value=0, max_value=100),
    item=st.sampled_from(list(_DEMO.keys())),
    price=st.integers(min_value=0, max_value=5_000_000),
    weight=st.integers(min_value=0, max_value=200_000),
)
def test_property_29_keep_it_trigger_conditions(reason, score, item, price, weight):
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(
                session,
                item_id=item,
                order_id=_DEMO[item],
                reason=reason,
                price=price,
                weight=weight,
            )

            # Independent oracle for the three conjoined trigger conditions
            # (R11.1): minor reason AND score >= keepItMinScore AND a positive
            # bounded Partial_Refund_Amount exists.
            min_score = int(GLOBAL_CONFIG["keep_it_min_score"])
            eligible = reason in MINOR_ISSUE_REASONS and score >= min_score
            rlc, div = compute_economics(rr.itemCategory, weight, price, score)
            amount = keep_it.compute_partial_refund_amount(price, rlc, div)
            expected_present = eligible and amount is not None

            result = keep_it.evaluate_keep_it(
                session, rr, score=score, client=_client()
            )

            # Presented iff and only iff all trigger conditions hold.
            assert result.presented is expected_present

            if expected_present:
                # An offer was persisted in PRESENTED state with the bounded
                # amount and the request moved to KEEP_IT_OFFERED.
                assert result.offer is not None
                assert result.offer.state == KeepItOfferState.PRESENTED
                assert result.offer.partialRefundAmountMinor == amount
                assert result.offer.partialRefundAmountMinor > 0
                assert rr.status == ReturnStatus.KEEP_IT_OFFERED
                persisted = session.scalar(
                    select(KeepItOffer).where(
                        KeepItOffer.returnRequestId == rr.returnRequestId
                    )
                )
                assert persisted is not None
            else:
                # No offer; the request routed to the Decision_Engine with
                # KEEP_IT excluded (never reselected).
                assert result.offer is None
                assert result.decision is not None
                assert "KEEP_IT" in (rr.excludedDispositions or [])
                if result.decision.ok:
                    assert result.decision.final != Disposition.KEEP_IT
                no_offer = session.scalar(
                    select(KeepItOffer).where(
                        KeepItOffer.returnRequestId == rr.returnRequestId
                    )
                )
                assert no_offer is None
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 30 — Partial_Refund_Amount bounds and net-profit invariant
# ===========================================================================
# Feature: secondlife-ai, Property 30: Partial_Refund_Amount bounds and net-profit invariant
# Validates: Requirements 11.2, 11.3
@given(
    price=st.integers(min_value=0, max_value=5_000_000),
    rlc=st.integers(min_value=0, max_value=5_000_000),
    div=st.integers(min_value=0, max_value=5_000_000),
)
def test_property_30_partial_refund_amount_bounds(price, rlc, div):
    amount = keep_it.compute_partial_refund_amount(price, rlc, div)
    if amount is not None:
        # A > 0 (R11.2)
        assert amount > 0
        # A < P (R11.2)
        assert amount < price
        # A < RLC (R11.2)
        assert amount < rlc
        # A + DIV <= RLC (R11.3)
        assert amount + div <= rlc
    else:
        # If None, it means no valid amount exists satisfying bounds
        cap = rlc - div
        upper = min(price - 1, rlc - 1, cap)
        assert upper < 1


# ===========================================================================
# Property 31 — Keep It acceptance side-effects are bounded
# ===========================================================================
# Feature: secondlife-ai, Property 31: Keep It acceptance side-effects are bounded
# Validates: Requirements 11.5, 11.9
@settings(suppress_health_check=[HealthCheck.filter_too_much])
@given(
    item=st.sampled_from(list(_DEMO.keys())),
    score=st.integers(min_value=80, max_value=100),
)
def test_property_31_keep_it_acceptance_side_effects(item, score):
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(
                session,
                item_id=item,
                order_id=_DEMO[item],
                reason=ReturnReason.MINOR_DEFECT,
                price=10000,
                weight=50000,
            )
            
            eval_result = keep_it.evaluate_keep_it(session, rr, score=score, client=_client())
            assume(eval_result.presented)
            
            # Accept the offer
            accept_outcome = keep_it.accept_offer(session, rr)
            
            assert accept_outcome.offer.state == KeepItOfferState.ACCEPTED
            assert accept_outcome.refund is not None
            assert accept_outcome.refund.amountMinor == eval_result.offer.partialRefundAmountMinor
            
            # Accept again -> idempotent (R11.9) - bounded side-effects
            accept_outcome_2 = keep_it.accept_offer(session, rr)
            assert accept_outcome_2.points_credited == 0
            assert accept_outcome_2.refund.amountMinor == accept_outcome.refund.amountMinor
            
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 32 — Keep It decline or expiry routes to the Decision_Engine
# ===========================================================================
# Feature: secondlife-ai, Property 32: Keep It decline or expiry routes to the Decision_Engine
# Validates: Requirements 11.6, 11.7
@settings(suppress_health_check=[HealthCheck.filter_too_much])
@given(
    item=st.sampled_from(list(_DEMO.keys())),
    score=st.integers(min_value=80, max_value=100),
    action=st.sampled_from(["DECLINE", "EXPIRE"]),
)
def test_property_32_keep_it_decline_expiry_routes(item, score, action):
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(
                session,
                item_id=item,
                order_id=_DEMO[item],
                reason=ReturnReason.MINOR_DEFECT,
                price=10000,
                weight=50000,
            )
            
            eval_result = keep_it.evaluate_keep_it(session, rr, score=score, client=_client())
            assume(eval_result.presented)

            if action == "DECLINE":
                decision = keep_it.decline_offer(session, rr, client=_client())
                expected_state = KeepItOfferState.DECLINED
            else:
                decision = keep_it.expire_offer(session, rr, client=_client())
                expected_state = KeepItOfferState.EXPIRED
            
            offer = session.scalar(select(KeepItOffer).where(KeepItOffer.returnRequestId == rr.returnRequestId))
            assert offer.state == expected_state
            
            assert decision is not None
            assert decision.ok
            assert decision.final != Disposition.KEEP_IT
            assert "KEEP_IT" in (rr.excludedDispositions or [])
            
        finally:
            session.close()
    finally:
        engine.dispose()
