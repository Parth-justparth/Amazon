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

from hypothesis import given
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
