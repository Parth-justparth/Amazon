"""Property-based tests for the Decision_Engine (task 8).

Covers design Correctness Properties 7, 8, 9, 10, 11, 12, 26, 27, and 28 for
``app.services.decision_engine``. Each test is tagged with the exact
``Feature: secondlife-ai, Property {n}: {text}`` comment and a
``Validates: Requirements ...`` line, and runs against the Hypothesis ``ci``
profile (>= 100 examples; see ``tests/conftest.py``).

Pure properties (economics + the rule-based engine) are exercised directly. The
hybrid-path properties build a fresh in-memory SQLite database per example
(tables + standard fixtures), create a scored return request, and drive the LLM
deterministically through the STUB_MODE decision config
(VALID / GUARDRAIL_VIOLATING / MALFORMED / EXCLUDED / TIMEOUT).
"""

from __future__ import annotations

import uuid

from hypothesis import assume, given
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import (
    Base,
    ConditionAssessment,
    DecisionSource,
    Disposition,
    DispositionRecord,
    Item,
    ItemCategory,
    Order,
    ReturnAction,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)
from app.fixtures.loader import load_all
from app.integrations.openai_client import (
    OpenAIVisionClient,
    StubDecisionConfig,
    StubDecisionMode,
)
from app.services.decision_engine import (
    FINAL_DISPOSITIONS,
    DecisionFailure,
    GuardrailConfig,
    compute_depreciated_item_value,
    compute_economics,
    compute_reverse_logistics_cost,
    decide_and_record,
    decide_rule_based,
    re_evaluate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_CATEGORIES = list(ItemCategory)
_FINAL_VALUES = {d.value for d in FINAL_DISPOSITIONS}


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
    item_id: str = "item_elec_01",
    order_id: str = "ord_1001",
    category: ItemCategory | None = None,
    price: int | None = None,
    weight: int | None = None,
    excluded: list[str] | None = None,
    score: int | None = None,
    status: ReturnStatus = ReturnStatus.SCORED,
) -> ReturnRequest:
    """Create a return request (and optional assessment) in SCORED state."""

    item = session.get(Item, item_id)
    order = session.get(Order, order_id)
    rr = ReturnRequest(
        returnRequestId=f"rr_{uuid.uuid4().hex[:10]}",
        orderId=order_id,
        itemId=item_id,
        customerId=order.customerId,
        reason=ReturnReason.DEFECTIVE,
        returnAction=ReturnAction.REPLACEMENT,
        status=status,
        itemCategory=category or item.category,
        purchasePriceMinor=price if price is not None else item.purchasePriceMinor,
        currency=order.currency,
        weightGrams=weight if weight is not None else item.weightGrams,
        paymentMethod=order.paymentMethod,
        sellerType=order.sellerType,
        returnWindowStart=order.deliveryDate,
        excludedDispositions=list(excluded or []),
    )
    session.add(rr)
    session.flush()
    if score is not None:
        session.add(
            ConditionAssessment(
                assessmentId=f"ca_{uuid.uuid4().hex[:10]}",
                returnRequestId=rr.returnRequestId,
                secondLifeScore=score,
                conditionSummary="seed",
                photoCount=1,
                modelVersion="test",
            )
        )
        session.flush()
    return rr


def _client(mode: StubDecisionMode, disposition: str | None = None) -> OpenAIVisionClient:
    """Build a STUB_MODE vision client driven by an explicit decision config."""

    return OpenAIVisionClient(
        decision_config=StubDecisionConfig(mode=mode, disposition=disposition)
    )


# ===========================================================================
# Property 7 — pure economics
# ===========================================================================
# Feature: secondlife-ai, Property 7: Depreciated value monotonicity and non-negative economics
# Validates: Requirements 3.1
@given(
    category=st.sampled_from(_ALL_CATEGORIES),
    price=st.integers(min_value=0, max_value=5_000_000),
    weight=st.integers(min_value=0, max_value=200_000),
    score_a=st.integers(min_value=0, max_value=100),
    score_b=st.integers(min_value=0, max_value=100),
)
def test_property_7_economics_nonneg_and_monotonic(
    category, price, weight, score_a, score_b
) -> None:
    rlc = compute_reverse_logistics_cost(category, weight)
    div_a = compute_depreciated_item_value(price, category, score_a)
    div_b = compute_depreciated_item_value(price, category, score_b)

    # Non-negative integer economics in the order currency (R3.1).
    assert isinstance(rlc, int) and rlc >= 0
    assert isinstance(div_a, int) and div_a >= 0
    assert isinstance(div_b, int) and div_b >= 0

    # Depreciated value is non-decreasing in the score (all else equal).
    lo, hi = sorted((score_a, score_b))
    assert compute_depreciated_item_value(
        price, category, lo
    ) <= compute_depreciated_item_value(price, category, hi)


# ===========================================================================
# Property 8 — rule determinism + exactly one final disposition
# ===========================================================================
# Feature: secondlife-ai, Property 8: Rule-engine determinism and exactly one final disposition
# Validates: Requirements 3.2
@given(
    score=st.integers(min_value=0, max_value=100),
    rlc=st.integers(min_value=0, max_value=2_000_000),
    div=st.integers(min_value=0, max_value=2_000_000),
    weight=st.integers(min_value=0, max_value=200_000),
    category=st.sampled_from(_ALL_CATEGORIES),
    mode=st.sampled_from(list(StubDecisionMode)),
)
def test_property_8_rule_determinism_single_disposition(
    score, rlc, div, weight, category, mode
) -> None:
    # Pure rule engine: complete inputs -> exactly one final disposition, and
    # repeated calls are identical (deterministic, pure function).
    first = decide_rule_based(score, rlc, div, weight, category, [])
    second = decide_rule_based(score, rlc, div, weight, category, [])
    assert first == second
    assert isinstance(first, Disposition)
    assert first in FINAL_DISPOSITIONS

    # Hybrid path: regardless of LLM behavior, exactly one final disposition is
    # recorded with a decisionSource in {LLM, RULE_FALLBACK}.
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(session, score=score)
            outcome = decide_and_record(session, rr, client=_client(mode))
            assert outcome.ok is True
            records = list(
                session.scalars(
                    select(DispositionRecord).where(
                        DispositionRecord.returnRequestId == rr.returnRequestId
                    )
                )
            )
            assert len(records) == 1
            rec = records[0]
            assert rec.decisionSource in (DecisionSource.LLM, DecisionSource.RULE_FALLBACK)
            assert rec.selected in FINAL_DISPOSITIONS
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 9 — threshold rules select the specified disposition
# ===========================================================================
# Feature: secondlife-ai, Property 9: Threshold rules select the specified disposition (rule engine)
# Validates: Requirements 3.3, 3.4, 3.5
@given(
    case=st.sampled_from(["warehouse", "resale", "donation"]),
    category=st.sampled_from(_ALL_CATEGORIES),
    base=st.integers(min_value=0, max_value=1_000_000),
    delta=st.integers(min_value=1, max_value=500_000),
    high_score=st.integers(min_value=80, max_value=100),
    low_score=st.integers(min_value=0, max_value=79),
    heavy_weight=st.integers(min_value=10_000, max_value=200_000),
)
def test_property_9_threshold_rules(
    case, category, base, delta, high_score, low_score, heavy_weight
) -> None:
    if case == "warehouse":
        # score >= 80 AND value > cost -> WAREHOUSE_RETURN (R3.3).
        rlc, div = base, base + delta
        result = decide_rule_based(high_score, rlc, div, 1_000, category, [])
        assert result == Disposition.WAREHOUSE_RETURN
    elif case == "resale":
        # score >= 80 AND weight >= 10 kg AND cost > value -> HYPERLOCAL_RESALE (R3.4).
        div, rlc = base, base + delta
        result = decide_rule_based(high_score, rlc, div, heavy_weight, category, [])
        assert result == Disposition.HYPERLOCAL_RESALE
    else:
        # 0 <= score <= 79 AND cost >= 50% of value -> GREEN_DONATION (R3.5).
        div = base + delta  # strictly positive value
        rlc = (div + 1) // 2  # ceil(0.5 * div) guarantees rlc >= 0.5 * div
        result = decide_rule_based(low_score, rlc, div, 1_000, category, [])
        assert result == Disposition.GREEN_DONATION


# ===========================================================================
# Property 10 — decision audit completeness
# ===========================================================================
# Feature: secondlife-ai, Property 10: Decision audit completeness
# Validates: Requirements 3.6
@given(
    score=st.integers(min_value=0, max_value=100),
    mode=st.sampled_from(list(StubDecisionMode)),
)
def test_property_10_decision_audit_completeness(score, mode) -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(session, score=score)
            expected_rlc, expected_div = compute_economics(
                rr.itemCategory, rr.weightGrams, rr.purchasePriceMinor, score
            )
            outcome = decide_and_record(session, rr, client=_client(mode))
            assert outcome.ok is True
            rec = outcome.record

            # Inputs used to decide are recorded faithfully (R3.6).
            assert rec.secondLifeScore == score
            assert rec.reverseLogisticsCostMinor == expected_rlc
            assert rec.depreciatedItemValueMinor == expected_div
            assert rec.weightGrams == rr.weightGrams
            assert rec.itemCategory == rr.itemCategory

            # Final disposition + valid decisionSource + rule disposition present.
            assert rec.selected in FINAL_DISPOSITIONS
            assert rec.decisionSource in (DecisionSource.LLM, DecisionSource.RULE_FALLBACK)
            assert rec.ruleDisposition in FINAL_DISPOSITIONS

            if rec.decisionSource is DecisionSource.LLM:
                # An LLM-sourced decision keeps the LLM disposition + reasoning.
                assert rec.llmDisposition is not None
                assert rec.llmReasoning is not None
            else:
                # RULE_FALLBACK: either a guardrail override (LLM fields retained)
                # or an LLM failure (LLM fields null). Both are valid audits, but
                # null disposition must pair with null reasoning.
                if rec.llmDisposition is None:
                    assert rec.llmReasoning is None
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 11 — decision failure on missing input
# ===========================================================================
# Feature: secondlife-ai, Property 11: Decision failure on missing input
# Validates: Requirements 3.7
@given(
    missing_field=st.sampled_from(
        ["secondLifeScore", "reverseLogisticsCost", "depreciatedItemValue",
         "weightGrams", "itemCategory"]
    ),
    score=st.integers(min_value=0, max_value=100),
    rlc=st.integers(min_value=0, max_value=1_000_000),
    div=st.integers(min_value=0, max_value=1_000_000),
    weight=st.integers(min_value=0, max_value=200_000),
    category=st.sampled_from(_ALL_CATEGORIES),
)
def test_property_11_decision_failure_on_missing_input(
    missing_field, score, rlc, div, weight, category
) -> None:
    inputs = {
        "secondLifeScore": score,
        "reverseLogisticsCost": rlc,
        "depreciatedItemValue": div,
        "weightGrams": weight,
        "itemCategory": category,
    }
    inputs[missing_field] = None

    result = decide_rule_based(
        inputs["secondLifeScore"],
        inputs["reverseLogisticsCost"],
        inputs["depreciatedItemValue"],
        inputs["weightGrams"],
        inputs["itemCategory"],
        [],
    )
    # A decision-failure identifying a missing field; no disposition selected.
    assert isinstance(result, DecisionFailure)
    assert result.missing in inputs
    assert inputs[result.missing] is None


# ===========================================================================
# Property 12 — re-evaluation never reselects an excluded disposition
# ===========================================================================
# Feature: secondlife-ai, Property 12: Re-evaluation never reselects an excluded disposition
# Validates: Requirements 5.7, 5.8, 7.7
@given(
    score=st.integers(min_value=0, max_value=100),
    item=st.sampled_from(["item_elec_01", "item_appl_01", "item_foot_01"]),
)
def test_property_12_reevaluation_excludes(score, item) -> None:
    orders = {
        "item_elec_01": "ord_1001",
        "item_appl_01": "ord_1002",
        "item_foot_01": "ord_1003",
    }
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(session, item_id=item, order_id=orders[item], score=score)
            first = decide_and_record(session, rr, client=_client(StubDecisionMode.VALID))
            assert first.ok is True
            excluded_seen = {first.final.value}

            # Re-evaluate excluding the first disposition.
            second = re_evaluate(
                session, rr, first.final, client=_client(StubDecisionMode.VALID)
            )
            assert second.ok is True
            assert second.final.value not in excluded_seen
            assert second.final.value not in (rr.excludedDispositions[:-1] or [])
            # The first disposition is now excluded and never reselected.
            assert second.final != first.final
            excluded_seen.add(second.final.value)

            # Re-evaluate again excluding the second; if it still resolves, the
            # result is none of the previously excluded dispositions.
            third = re_evaluate(
                session, rr, second.final, client=_client(StubDecisionMode.VALID)
            )
            if third.ok:
                assert third.final.value not in excluded_seen
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 26 — final disposition is always valid
# ===========================================================================
# Feature: secondlife-ai, Property 26: Final disposition is always valid
# Validates: Requirements 3.2
@given(
    score=st.integers(min_value=0, max_value=100),
    mode=st.sampled_from(list(StubDecisionMode)),
    disposition=st.one_of(
        st.none(),
        st.sampled_from(list(_FINAL_VALUES)),
        st.sampled_from(["KEEP_IT", "GARBAGE", "", "warehouse_return", "???"]),
    ),
)
def test_property_26_final_disposition_always_valid(score, mode, disposition) -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(session, score=score)
            client = OpenAIVisionClient(
                decision_config=StubDecisionConfig(mode=mode, disposition=disposition)
            )
            outcome = decide_and_record(session, rr, client=client)
            assert outcome.ok is True
            # Regardless of LLM output (malformed/missing/out-of-enum), the final
            # recorded disposition is always one of the three valid values.
            assert outcome.final in FINAL_DISPOSITIONS
            assert outcome.record.selected.value in _FINAL_VALUES
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 27 — rule-fallback equivalence
# ===========================================================================
# Feature: secondlife-ai, Property 27: Rule-fallback equivalence
# Validates: Requirements 3.2, 3.3, 3.4, 3.5
@given(
    score=st.integers(min_value=0, max_value=100),
    mode=st.sampled_from(list(StubDecisionMode)),
)
def test_property_27_rule_fallback_equivalence(score, mode) -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(session, score=score)
            rlc, div = compute_economics(
                rr.itemCategory, rr.weightGrams, rr.purchasePriceMinor, score
            )
            expected_rule = decide_rule_based(
                score, rlc, div, rr.weightGrams, rr.itemCategory, []
            )
            outcome = decide_and_record(session, rr, client=_client(mode))
            assert outcome.ok is True
            rec = outcome.record

            # When the decision came from the rule fallback, the final disposition
            # equals decide_rule_based(...) on the same inputs.
            if rec.decisionSource is DecisionSource.RULE_FALLBACK:
                assert isinstance(expected_rule, Disposition)
                assert rec.selected == expected_rule
                assert rec.ruleDisposition == expected_rule
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 28 — safety guardrail is never violated
# ===========================================================================
# Feature: secondlife-ai, Property 28: Safety guardrail is never violated
# Validates: Requirements 3.2
@given(
    score=st.integers(min_value=0, max_value=100),
    category=st.sampled_from(_ALL_CATEGORIES),
    price=st.integers(min_value=0, max_value=200_000),
    weight=st.integers(min_value=0, max_value=200_000),
)
def test_property_28_safety_guardrail_never_violated(
    score, category, price, weight
) -> None:
    # Only meaningful when reverse-logistics cost strictly exceeds value.
    rlc, div = compute_economics(category, weight, price, score)
    assume(rlc > div)

    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_rr(
                session, category=category, price=price, weight=weight, score=score
            )
            # The LLM picks WAREHOUSE_RETURN, which violates the hard constraint.
            client = OpenAIVisionClient(
                decision_config=StubDecisionConfig(
                    mode=StubDecisionMode.GUARDRAIL_VIOLATING,
                    disposition=Disposition.WAREHOUSE_RETURN.value,
                )
            )
            outcome = decide_and_record(
                session, rr, client=client, guardrail=GuardrailConfig(enabled=True)
            )
            assert outcome.ok is True
            # The guardrail forces the rule disposition; never WAREHOUSE_RETURN.
            assert outcome.final != Disposition.WAREHOUSE_RETURN
            assert outcome.record.decisionSource is DecisionSource.RULE_FALLBACK
        finally:
            session.close()
    finally:
        engine.dispose()
