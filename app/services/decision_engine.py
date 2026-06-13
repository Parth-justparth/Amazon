"""Decision_Engine — hybrid LLM-primary disposition selection (R3).

This module implements the design "Decision Engine Methodology" section. It is
built bottom-up:

* **Economics** (R3.1) — :func:`compute_reverse_logistics_cost` and
  :func:`compute_depreciated_item_value` are pure functions over the recorded
  inputs producing integer minor-unit amounts.
* **Rule-based engine** (R3.2-3.5) — :func:`decide_rule_based` is a pure,
  deterministic function that selects exactly one disposition (or reports a
  missing input). It is retained as the fallback, the always-on shadow
  decision, and the safety guardrail.
* **Hybrid path** (R3.2, 3.6, 3.7) — :func:`decide_and_record` computes the
  economics, calls the LLM for the primary disposition, computes the rule-based
  shadow, applies the configurable safety guardrail, falls back to the rule
  disposition on any LLM failure/timeout/malformed/invalid/excluded output, and
  records exactly one :class:`DispositionRecord` with full audit. The FastAPI
  route ``POST /returns/{id}/decision`` wraps it.
* **Re-evaluation** (R5.7, 5.8, 7.7) — :func:`re_evaluate` re-runs the engine
  with a prior disposition added to ``excludedDispositions`` and never
  reselects an excluded disposition.

Category economics resolution
------------------------------
:data:`DECISION_ENGINE_CATEGORY_CONFIG` is keyed by the three *demo* categories
(ELECTRONICS / HOME_APPLIANCES / FOOTWEAR). The seven *policy* categories
(R14/R15) are resolved onto those economics via
:data:`ECONOMICS_CATEGORY_RESOLUTION`:

* ``MOBILES_LAPTOPS_ELECTRONICS`` → ELECTRONICS economics (inverse of
  :data:`~app.fixtures.seed_data.DEMO_TO_POLICY_CATEGORY`),
* ``HOME_KITCHEN_APPLIANCES``     → HOME_APPLIANCES economics,
* ``CLOTHING_FOOTWEAR``           → FOOTWEAR economics,
* the remaining lightweight policy categories without a demo counterpart
  (``BOOKS``, ``GROCERY_PERISHABLES``, ``BEAUTY_PERSONAL_CARE``,
  ``SOFTWARE_VIDEO_GAMES_MUSIC``) default to FOOTWEAR (small-item) economics.

The three demo categories map to themselves. Every :class:`ItemCategory` thus
resolves to a concrete economics config, keeping the engine total.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    ConditionAssessment,
    DecisionSource,
    Disposition,
    DispositionRecord,
    Item,
    ItemCategory,
    ReturnRequest,
    ReturnStatus,
)
from app.domain.money import to_iso8601
from app.domain.repository import get_session_factory
from app.fixtures.seed_data import (
    DECISION_ENGINE_CATEGORY_CONFIG,
    DEMO_TO_POLICY_CATEGORY,
)
from app.integrations.openai_client import (
    OpenAIVisionClient,
    VALID_DISPOSITIONS,
    get_vision_client,
)

__all__ = [
    "router",
    "get_db",
    "compute_reverse_logistics_cost",
    "compute_depreciated_item_value",
    "compute_economics",
    "decide_rule_based",
    "fallback_select",
    "validate_disposition",
    "violates_safety_override",
    "decide_and_record",
    "re_evaluate",
    "DecisionFailure",
    "InvalidDisposition",
    "LLMDecisionError",
    "GuardrailConfig",
    "DecisionOutcome",
    "ECONOMICS_CATEGORY_RESOLUTION",
    "FINAL_DISPOSITIONS",
]


# ---------------------------------------------------------------------------
# Category economics resolution
# ---------------------------------------------------------------------------

#: The three valid *final* dispositions the engine may record (R3.2). Keep It is
#: handled by a separate service ahead of the engine and is never produced here.
FINAL_DISPOSITIONS: tuple[Disposition, ...] = (
    Disposition.WAREHOUSE_RETURN,
    Disposition.HYPERLOCAL_RESALE,
    Disposition.GREEN_DONATION,
)


def _build_economics_resolution() -> dict[ItemCategory, ItemCategory]:
    """Resolve every :class:`ItemCategory` onto a demo economics category.

    Demo categories map to themselves; policy categories with a demo counterpart
    invert :data:`DEMO_TO_POLICY_CATEGORY`; the remaining lightweight policy
    categories default to FOOTWEAR (small-item) economics. See module docstring.
    """

    resolution: dict[ItemCategory, ItemCategory] = {}
    # Demo categories that carry their own economics config map to themselves.
    for demo in DECISION_ENGINE_CATEGORY_CONFIG:
        resolution[demo] = demo
    # Invert the demo->policy map so each mapped policy category reuses the
    # corresponding demo economics.
    for demo, policy_cat in DEMO_TO_POLICY_CATEGORY.items():
        resolution[policy_cat] = demo
    # Default any remaining categories to FOOTWEAR (lightweight small-item) economics.
    for category in ItemCategory:
        resolution.setdefault(category, ItemCategory.FOOTWEAR)
    return resolution


#: Maps any ItemCategory to the demo category whose economics config applies.
ECONOMICS_CATEGORY_RESOLUTION: dict[ItemCategory, ItemCategory] = (
    _build_economics_resolution()
)


def _economics_config(category: ItemCategory) -> dict:
    """Return the per-category economics constants for ``category``."""

    resolved = ECONOMICS_CATEGORY_RESOLUTION[category]
    return DECISION_ENGINE_CATEGORY_CONFIG[resolved]


# ---------------------------------------------------------------------------
# Economics (R3.1)
# ---------------------------------------------------------------------------


def compute_reverse_logistics_cost(category: ItemCategory, weight_grams: int) -> int:
    """Compute the Reverse_Logistics_Cost in integer minor units (R3.1).

    ``base_handling_fee[category] + per_kg_freight_rate * (weightGrams / 1000)
    + inspection_fee[category]``. Weight-driven freight is what makes bulky
    appliances expensive to return. The result is a non-negative integer.
    """

    cfg = _economics_config(category)
    weight = max(0, int(weight_grams))
    freight = round(cfg["per_kg_freight_rate"] * weight / 1000)
    total = int(cfg["base_handling_fee"]) + int(freight) + int(cfg["inspection_fee"])
    return max(0, total)


def compute_depreciated_item_value(
    purchase_price_minor: int, category: ItemCategory, score: int
) -> int:
    """Compute the Depreciated_Item_Value in integer minor units (R3.1).

    ``round(purchasePriceMinor * category_base_retention[category] *
    (score / 100))``. The result is a non-negative integer and is non-decreasing
    in ``score`` (all else equal), since the price and retention factor are
    non-negative.
    """

    cfg = _economics_config(category)
    price = max(0, int(purchase_price_minor))
    clamped_score = max(0, min(100, int(score)))
    value = round(price * cfg["category_base_retention"] * (clamped_score / 100))
    return max(0, int(value))


def compute_economics(
    category: ItemCategory, weight_grams: int, purchase_price_minor: int, score: int
) -> tuple[int, int]:
    """Return ``(reverse_logistics_cost, depreciated_item_value)`` (R3.1)."""

    rlc = compute_reverse_logistics_cost(category, weight_grams)
    div = compute_depreciated_item_value(purchase_price_minor, category, score)
    return rlc, div


# ---------------------------------------------------------------------------
# Rule-based fallback / guardrail engine (R3.2-3.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionFailure:
    """Reported by :func:`decide_rule_based` when a required input is missing (R3.7)."""

    missing: str


class InvalidDisposition(Exception):
    """Raised by :func:`validate_disposition` for an invalid/excluded LLM choice."""


class LLMDecisionError(Exception):
    """Raised for an LLM decision-call failure (drives the rule fallback)."""


def _excluded_set(excluded: list[str] | None) -> set[str]:
    """Normalize an excluded-dispositions list into a set of enum *values*."""

    result: set[str] = set()
    for item in excluded or []:
        result.add(item.value if isinstance(item, Disposition) else str(item))
    return result


def fallback_select(
    rlc: int, div: int, excluded: set[str]
) -> Disposition | None:
    """Choose the economically optimal non-excluded disposition (totality).

    Prefers ``WAREHOUSE_RETURN`` when value beats cost (recover value
    officially); otherwise prefers ``HYPERLOCAL_RESALE`` (avoid freight, still
    refund) then ``GREEN_DONATION`` (avoid net-loss logistics). When ``div`` does
    not exceed ``rlc``, warehouse is considered only as a last resort so the
    economic guardrail is respected. Returns ``None`` only when every disposition
    is excluded (the caller then flags MANUAL).
    """

    if div > rlc:
        order = (
            Disposition.WAREHOUSE_RETURN,
            Disposition.HYPERLOCAL_RESALE,
            Disposition.GREEN_DONATION,
        )
    else:
        order = (
            Disposition.HYPERLOCAL_RESALE,
            Disposition.GREEN_DONATION,
            Disposition.WAREHOUSE_RETURN,
        )
    for disposition in order:
        if disposition.value not in excluded:
            return disposition
    return None


def decide_rule_based(
    score: int | None,
    rlc: int | None,
    div: int | None,
    weight_grams: int | None,
    category: ItemCategory | None,
    excluded: list[str] | None = None,
) -> Disposition | DecisionFailure:
    """Deterministically select exactly one disposition (R3.2-3.5, R3.7).

    A pure function: identical inputs always yield the identical result. Returns
    a :class:`DecisionFailure` naming the first missing input (R3.7); otherwise
    applies the threshold rules in order and falls back to
    :func:`fallback_select` for inputs the three explicit rules do not cover.
    """

    # R3.7: completeness guard — name the first missing required input.
    for name, value in (
        ("secondLifeScore", score),
        ("reverseLogisticsCost", rlc),
        ("depreciatedItemValue", div),
        ("weightGrams", weight_grams),
        ("itemCategory", category),
    ):
        if value is None:
            return DecisionFailure(missing=name)

    excluded_set = _excluded_set(excluded)
    weight_kg = weight_grams / 1000

    # R3.3: high condition AND value beats logistics cost -> warehouse.
    if (
        score >= 80
        and div > rlc
        and Disposition.WAREHOUSE_RETURN.value not in excluded_set
    ):
        return Disposition.WAREHOUSE_RETURN

    # R3.4: high condition, bulky, logistics cost beats value -> resale.
    if (
        score >= 80
        and weight_kg >= 10
        and rlc > div
        and Disposition.HYPERLOCAL_RESALE.value not in excluded_set
    ):
        return Disposition.HYPERLOCAL_RESALE

    # R3.5: lower condition AND logistics cost >= 50% of value -> donation.
    if (
        0 <= score <= 79
        and rlc >= 0.5 * div
        and Disposition.GREEN_DONATION.value not in excluded_set
    ):
        return Disposition.GREEN_DONATION

    # Totality: choose the most economical remaining disposition.
    selected = fallback_select(rlc, div, excluded_set)
    if selected is None:
        # Every disposition excluded — terminal; surface as a failure so the
        # caller can flag MANUAL (re-evaluation grows the excluded set).
        return DecisionFailure(missing="availableDisposition")
    return selected


# ---------------------------------------------------------------------------
# LLM validation + safety guardrail
# ---------------------------------------------------------------------------


def validate_disposition(value: str | None, excluded: list[str] | None) -> Disposition:
    """Validate an LLM disposition string (R3.2).

    Raises :class:`InvalidDisposition` when ``value`` is missing, not one of the
    three valid platform dispositions, or present in the excluded set.
    """

    if value is None or value not in VALID_DISPOSITIONS:
        raise InvalidDisposition(f"Invalid disposition from LLM: {value!r}")
    if value in _excluded_set(excluded):
        raise InvalidDisposition(f"Excluded disposition from LLM: {value!r}")
    return Disposition(value)


@dataclass(frozen=True)
class GuardrailConfig:
    """Configuration for the hard economic safety override (R3, Property 28)."""

    enabled: bool = True


def violates_safety_override(
    disposition: Disposition, rlc: int, div: int, config: GuardrailConfig
) -> bool:
    """Whether ``disposition`` violates the enabled hard economic constraint.

    Reference rule: the final disposition SHALL NOT be ``WAREHOUSE_RETURN`` when
    ``reverse_logistics_cost > depreciated_item_value`` (returning the item would
    cost more than it is worth).
    """

    return (
        config.enabled
        and disposition == Disposition.WAREHOUSE_RETURN
        and rlc > div
    )


# ---------------------------------------------------------------------------
# Hybrid decision + audit (R3.2, 3.6, 3.7)
# ---------------------------------------------------------------------------


@dataclass
class DecisionOutcome:
    """Outcome of :func:`decide_and_record`.

    ``ok`` is True only when a final disposition was selected and a
    :class:`DispositionRecord` was written. On a missing-input failure
    (``status_code == 422``) no record is written and the request status is
    unchanged (R3.7).
    """

    ok: bool
    status_code: int
    return_request: ReturnRequest | None = None
    record: DispositionRecord | None = None
    final: Disposition | None = None
    error_code: str | None = None
    message: str | None = None
    missing: str | None = None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _latest_score(session: Session, return_request_id: str) -> int | None:
    """Return the most recent recorded SecondLife_Score, or None if unscored."""

    assessment = session.scalar(
        select(ConditionAssessment)
        .where(ConditionAssessment.returnRequestId == return_request_id)
        .order_by(ConditionAssessment.createdAt.desc())
    )
    return assessment.secondLifeScore if assessment is not None else None


def _photo_set(session: Session, rr: ReturnRequest) -> str:
    """Resolve the photo-set key driving the STUB_MODE decision call."""

    item = session.get(Item, rr.itemId)
    refs = (item.photoRefs if item is not None else None) or []
    return refs[0] if refs else ""


def decide_and_record(
    session: Session,
    rr: ReturnRequest,
    *,
    client: OpenAIVisionClient | None = None,
    guardrail: GuardrailConfig | None = None,
    score: int | None = None,
) -> DecisionOutcome:
    """Run the hybrid decision and record exactly one audit row (R3.2, 3.6, 3.7).

    Args:
        session: Open session; rows are flushed (not committed) so callers own
            the transaction boundary.
        rr: The return request being decided.
        client: Optional vision client; defaults to a settings-bound client
            (STUB_MODE in CI/demo).
        guardrail: Optional safety-guardrail config; defaults to enabled.
        score: Optional explicit score override (tests). When omitted, the most
            recent recorded SecondLife_Score is used; absence yields R3.7.

    Returns:
        A :class:`DecisionOutcome`.
    """

    guardrail = guardrail or GuardrailConfig()
    excluded = list(rr.excludedDispositions or [])

    # Resolve the authoritative SecondLife_Score (recorded by assessment).
    effective_score = score if score is not None else _latest_score(
        session, rr.returnRequestId
    )

    category = rr.itemCategory
    weight_grams = rr.weightGrams
    price = rr.purchasePriceMinor

    # Economics. ``rlc`` never depends on the score; ``div`` does, so it is None
    # when the score is unavailable (drives the R3.7 completeness guard).
    rlc = compute_reverse_logistics_cost(category, weight_grams)
    div = (
        compute_depreciated_item_value(price, category, effective_score)
        if effective_score is not None
        else None
    )

    # Always compute the deterministic rule-based shadow/fallback first.
    rule_result = decide_rule_based(
        effective_score, rlc, div, weight_grams, category, excluded
    )
    if isinstance(rule_result, DecisionFailure):
        return DecisionOutcome(
            ok=False,
            status_code=422,
            return_request=rr,
            error_code="DECISION_FAILED",
            message=f"Required decision input is unavailable: {rule_result.missing}.",
            missing=rule_result.missing,
        )
    rule_disposition: Disposition = rule_result

    # Primary decision: ask the LLM, then validate + guardrail; fall back on any
    # failure/timeout/malformed/invalid/excluded output.
    vision = client or get_vision_client()
    llm_disposition: Disposition | None = None
    reasoning: str | None = None
    final: Disposition
    source: DecisionSource

    try:
        decision = vision.decide(
            _photo_set(session, rr),
            item_context={
                "category": category.value,
                "title": "",
                "purchasePriceMinor": price,
                "currency": rr.currency,
                "weightGrams": weight_grams,
            },
            economics={"reverseLogisticsCost": rlc, "depreciatedItemValue": div},
            excluded_dispositions=excluded,
        )
        validated = validate_disposition(decision.disposition, excluded)
        reasoning = decision.reasoning
        if violates_safety_override(validated, rlc, div, guardrail):
            # Guardrail override: keep the LLM disposition + reasoning for audit,
            # but use the rule disposition with decisionSource = RULE_FALLBACK.
            llm_disposition = validated
            final, source = rule_disposition, DecisionSource.RULE_FALLBACK
        else:
            llm_disposition = validated
            final, source = validated, DecisionSource.LLM
    except (TimeoutError, InvalidDisposition, LLMDecisionError):
        # On failure the LLM contributes nothing to the audit (null fields).
        llm_disposition, reasoning = None, None
        final, source = rule_disposition, DecisionSource.RULE_FALLBACK

    # Record exactly one decision: replace any prior record (re-evaluation).
    existing = session.scalar(
        select(DispositionRecord).where(
            DispositionRecord.returnRequestId == rr.returnRequestId
        )
    )
    if existing is not None:
        session.delete(existing)
        session.flush()

    record = DispositionRecord(
        dispositionId=_new_id("disp"),
        returnRequestId=rr.returnRequestId,
        selected=final,
        decisionSource=source,
        llmDisposition=llm_disposition,
        ruleDisposition=rule_disposition,
        llmReasoning=reasoning,
        secondLifeScore=effective_score,
        reverseLogisticsCostMinor=rlc,
        depreciatedItemValueMinor=div,
        weightGrams=weight_grams,
        itemCategory=category,
    )
    session.add(record)
    rr.status = ReturnStatus.DECIDED
    session.flush()

    return DecisionOutcome(
        ok=True,
        status_code=200,
        return_request=rr,
        record=record,
        final=final,
    )


def re_evaluate(
    session: Session,
    rr: ReturnRequest,
    excluded_disposition: Disposition | str,
    *,
    client: OpenAIVisionClient | None = None,
    guardrail: GuardrailConfig | None = None,
    score: int | None = None,
) -> DecisionOutcome:
    """Re-run the engine excluding a prior disposition (R5.7, R5.8, R7.7).

    Adds ``excluded_disposition`` to ``excludedDispositions``, resets the request
    to ``SCORED``, and re-invokes :func:`decide_and_record`. The excluded set
    strictly grows, so the process terminates and never reselects an excluded
    disposition.
    """

    value = (
        excluded_disposition.value
        if isinstance(excluded_disposition, Disposition)
        else str(excluded_disposition)
    )
    current = list(rr.excludedDispositions or [])
    if value not in current:
        current.append(value)
    # Reassign (not mutate) so SQLAlchemy detects the JSON column change.
    rr.excludedDispositions = current
    rr.status = ReturnStatus.SCORED
    session.flush()

    return decide_and_record(
        session, rr, client=client, guardrail=guardrail, score=score
    )


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["decision-engine"])


def get_db() -> Session:
    """FastAPI dependency yielding a session; commits on success, rolls back on error."""

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


def _serialize(rr: ReturnRequest, record: DispositionRecord) -> dict:
    """Render the design's POST /returns/{id}/decision 200 response."""

    return {
        "returnRequestId": rr.returnRequestId,
        "disposition": record.selected.value,
        "decisionSource": record.decisionSource.value,
        "llmDisposition": record.llmDisposition.value
        if record.llmDisposition is not None
        else None,
        "ruleDisposition": record.ruleDisposition.value,
        "llmReasoning": record.llmReasoning,
        "secondLifeScore": record.secondLifeScore,
        "reverseLogisticsCost": record.reverseLogisticsCostMinor,
        "depreciatedItemValue": record.depreciatedItemValueMinor,
        "weightGrams": record.weightGrams,
        "itemCategory": record.itemCategory.value,
        "currency": rr.currency,
        "decidedAt": to_iso8601(record.decidedAt),
    }


@router.post("/returns/{returnRequestId}/decision")
def post_decision(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Compute economics + hybrid decision and record one disposition (R3)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "RETURN_NOT_FOUND", "message": "Return request not found."},
        )

    outcome = decide_and_record(session, rr)

    if outcome.ok and outcome.record is not None:
        return _serialize(rr, outcome.record)

    detail: dict = {"error": outcome.error_code, "message": outcome.message}
    if outcome.missing is not None:
        detail["missing"] = outcome.missing
    raise HTTPException(status_code=outcome.status_code, detail=detail)
