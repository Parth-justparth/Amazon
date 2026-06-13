"""OpenAI vision client with deterministic ``STUB_MODE`` (R2, R3).

This module implements the two LLM call shapes described in the design
("Technology Stack" and "OpenAI Condition-Assessment Methodology"):

1. **Condition assessment** — :meth:`OpenAIVisionClient.assess_condition`
   returns ``{"secondLifeScore": int 0-100, "conditionSummary": str}``.
2. **Hybrid decision** — :meth:`OpenAIVisionClient.decide` returns
   ``{"secondLifeScore": int, "disposition": str, "reasoning": str}``. The
   decision shape is consumed by the Decision_Engine (task 8); the signature
   and the configurable stub are provided here so task 8 can drive the hybrid
   engine.

Both calls run at ``temperature = 0`` and record the pinned model version
(:attr:`Settings.openai_model_version`).

STUB_MODE (the default; enabled whenever :attr:`Settings.stub_mode` is True)
serves both calls deterministically from :data:`PHOTO_SCORE_FIXTURES` keyed by
the photo-set name carried on the return request's ``photoRefs``:

* a known photo set returns its fixed ``expected_score`` + ``summary``;
* the sentinel key ``photos_unscorable`` raises :class:`AssessmentFailed`
  (mapping to ``ASSESSMENT_FAILED`` / R2.7);
* any other (unmapped) key is served deterministically from a stable hash of
  the key, so tests and demos never hit the network.

Tests configure the hybrid decision response via :class:`StubDecisionConfig`
(valid / guardrail-violating / malformed / excluded / timeout), set either with
the constructor argument ``decision_config`` or the module-level
:func:`set_stub_decision_config`.

When ``stub_mode`` is False the real OpenAI path is structured (strict JSON,
temperature 0, pinned model) but intentionally minimal/guarded — the demo runs
in stub mode. No API key is required at import time.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.config import Settings, get_settings
from app.fixtures.seed_data import PHOTO_SCORE_FIXTURES

__all__ = [
    "AssessmentFailed",
    "AssessmentResult",
    "DecisionResult",
    "StubDecisionMode",
    "StubDecisionConfig",
    "OpenAIVisionClient",
    "get_vision_client",
    "set_stub_decision_config",
    "reset_stub_decision_config",
    "UNSCORABLE_PHOTO_SET",
    "VALID_DISPOSITIONS",
]

#: The sentinel photo-set key that deterministically yields ASSESSMENT_FAILED.
UNSCORABLE_PHOTO_SET = "photos_unscorable"

#: The three valid platform dispositions the decision call may return.
VALID_DISPOSITIONS: tuple[str, ...] = (
    "WAREHOUSE_RETURN",
    "HYPERLOCAL_RESALE",
    "GREEN_DONATION",
)


class AssessmentFailed(Exception):
    """Raised when the model cannot determine a score (maps to R2.7).

    The condition-assessment service catches this and returns a
    ``422 ASSESSMENT_FAILED`` requesting clearer re-upload.
    """


@dataclass(frozen=True)
class AssessmentResult:
    """Structured output of the condition-assessment call (R2.1, R2.3)."""

    secondLifeScore: int
    conditionSummary: str
    modelVersion: str


@dataclass(frozen=True)
class DecisionResult:
    """Structured output of the hybrid decision call (R3, task 8).

    ``disposition`` may be ``None`` or an out-of-enum string when the stub is
    configured to emit malformed/invalid output, so the Decision_Engine
    fallback path can be exercised. ``raw`` carries the unparsed payload for
    audit/debugging.
    """

    secondLifeScore: int | None
    disposition: str | None
    reasoning: str | None
    modelVersion: str
    raw: dict[str, Any] = field(default_factory=dict)


class StubDecisionMode(str, Enum):
    """How the STUB_MODE decision call should respond (task 8 drivers)."""

    #: Serve a valid disposition derived from the photo-set fixture.
    VALID = "VALID"
    #: Return a disposition that violates the hard economic guardrail.
    GUARDRAIL_VIOLATING = "GUARDRAIL_VIOLATING"
    #: Return malformed/unparseable output (missing/invalid fields).
    MALFORMED = "MALFORMED"
    #: Return a disposition that is in the excluded set (re-evaluation).
    EXCLUDED = "EXCLUDED"
    #: Raise a timeout/error to exercise the rule-based fallback.
    TIMEOUT = "TIMEOUT"


@dataclass
class StubDecisionConfig:
    """Configuration of the STUB_MODE hybrid-decision response (task 8).

    Attributes:
        mode: Which canned behavior to produce.
        disposition: Explicit disposition to emit (overrides the fixture-derived
            default) for VALID/GUARDRAIL_VIOLATING/EXCLUDED modes.
        reasoning: Reasoning string to emit.
        score: Explicit score override; when None the fixture score is used.
    """

    mode: StubDecisionMode = StubDecisionMode.VALID
    disposition: str | None = None
    reasoning: str | None = None
    score: int | None = None


# Module-level default stub decision config so task 8 / tests can drive the
# hybrid engine without constructing a client. The constructor arg takes
# precedence over this when supplied.
_DEFAULT_DECISION_CONFIG = StubDecisionConfig()
_module_decision_config: StubDecisionConfig = StubDecisionConfig()


def set_stub_decision_config(config: StubDecisionConfig) -> None:
    """Set the process-wide STUB_MODE decision configuration (task 8/tests)."""

    global _module_decision_config
    _module_decision_config = config


def reset_stub_decision_config() -> None:
    """Reset the process-wide STUB_MODE decision configuration to the default."""

    global _module_decision_config
    _module_decision_config = StubDecisionConfig()


def _clamp_score(value: int) -> int:
    """Coerce/clamp a score into the inclusive [0, 100] integer range."""

    return max(0, min(100, int(value)))


def _trim_summary(text: str) -> str:
    """Trim a condition summary to 1-500 chars, defaulting when empty (R2.3)."""

    cleaned = (text or "").strip()
    if not cleaned:
        cleaned = "No defects observed."
    return cleaned[:500]


def _deterministic_default_score(photo_set: str) -> int:
    """Derive a stable, deterministic score in [0, 100] from a photo-set key.

    Used for photo sets not present in :data:`PHOTO_SCORE_FIXTURES` so STUB_MODE
    stays fully deterministic and offline for arbitrary inputs.
    """

    digest = hashlib.sha256(photo_set.encode("utf-8")).hexdigest()
    return int(digest, 16) % 101


class OpenAIVisionClient:
    """Vision-capable client serving assessment + decision calls.

    In ``STUB_MODE`` (the default), results are served from fixtures with no
    network access. The live path is structured but intentionally minimal,
    since the demo runs in stub mode.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        decision_config: StubDecisionConfig | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._decision_config = decision_config

    # -- properties -------------------------------------------------------

    @property
    def stub_mode(self) -> bool:
        """Whether the client serves deterministic fixtures (no network)."""

        return self._settings.stub_mode

    @property
    def model_version(self) -> str:
        """The pinned model version recorded with every call (R2.2)."""

        return self._settings.openai_model_version

    @property
    def temperature(self) -> float:
        """Decoding temperature; 0 for deterministic output."""

        return self._settings.openai_temperature

    def _active_decision_config(self) -> StubDecisionConfig:
        """Resolve the decision config: constructor arg over module default."""

        if self._decision_config is not None:
            return self._decision_config
        return _module_decision_config

    # -- (a) condition assessment ----------------------------------------

    def assess_condition(
        self, photo_set: str, *, item_context: dict | None = None
    ) -> AssessmentResult:
        """Produce a SecondLife_Score + condition summary for ``photo_set``.

        Args:
            photo_set: The photo-set key (the return request's ``photoRefs``
                entry) used to look up the deterministic stub fixture.
            item_context: Optional item metadata (category, title) forwarded to
                the live model; ignored in STUB_MODE.

        Returns:
            An :class:`AssessmentResult` with an integer score in [0, 100] and a
            1-500 char summary.

        Raises:
            AssessmentFailed: When the photos are unscorable (R2.7).
        """

        if self.stub_mode:
            return self._stub_assess(photo_set)
        return self._live_assess(photo_set, item_context or {})

    def _stub_assess(self, photo_set: str) -> AssessmentResult:
        """Serve a deterministic assessment from the fixture map (STUB_MODE)."""

        if photo_set == UNSCORABLE_PHOTO_SET:
            raise AssessmentFailed(
                "The submitted photos could not be assessed; please re-upload "
                "clearer photos."
            )

        fixture = PHOTO_SCORE_FIXTURES.get(photo_set)
        if fixture is not None:
            return AssessmentResult(
                secondLifeScore=_clamp_score(fixture["expected_score"]),
                conditionSummary=_trim_summary(fixture["summary"]),
                modelVersion=self.model_version,
            )

        # Unmapped photo set -> deterministic, offline default.
        score = _deterministic_default_score(photo_set)
        return AssessmentResult(
            secondLifeScore=score,
            conditionSummary=_trim_summary(
                f"Condition assessed from photo set '{photo_set}'."
            ),
            modelVersion=self.model_version,
        )

    def _live_assess(self, photo_set: str, item_context: dict) -> AssessmentResult:
        """Structure for the real OpenAI assessment call (guarded; minimal)."""

        client = self._require_live_client()
        
        # If photo_set is our local file paths, format them as base64 images
        content_blocks = []
        content_blocks.append({"type": "text", "text": f"You are an expert return inspector. Analyze these photos for an item of category: {item_context.get('category')}. Provide a strict JSON response containing 'secondLifeScore' (an integer 0-100 indicating condition, 100=pristine, 0=destroyed) and 'conditionSummary' (a 1-500 char string describing the exact visual damage or condition)."})
        
        if "|" in photo_set or "\\" in photo_set or "/" in photo_set:
            paths = photo_set.split("|")
            import base64
            for path in paths:
                try:
                    with open(path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                        })
                except Exception:
                    pass
        else:
            # Fallback for unexpected photo_set
            content_blocks.append({"type": "text", "text": "Assume the item condition matches this ID: " + photo_set})

        import json
        try:
            resp = client.chat.completions.create(
                model=self._settings.openai_model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": content_blocks}]
            )
            parsed = json.loads(resp.choices[0].message.content)
            score = int(parsed.get("secondLifeScore", 50))
            summary = str(parsed.get("conditionSummary", "Condition analyzed."))
            
            return AssessmentResult(
                secondLifeScore=_clamp_score(score),
                conditionSummary=_trim_summary(summary),
                modelVersion=self.model_version
            )
        except Exception as exc:
            raise AssessmentFailed(f"OpenAI analysis failed: {str(exc)}")

    # -- (b) hybrid decision (consumed by task 8) ------------------------

    def decide(
        self,
        photo_set: str,
        *,
        item_context: dict | None = None,
        economics: dict | None = None,
        excluded_dispositions: list[str] | None = None,
    ) -> DecisionResult:
        """Produce a combined score + disposition + reasoning (R3, task 8).

        In STUB_MODE the behavior is governed by the active
        :class:`StubDecisionConfig` so the Decision_Engine's guardrail/fallback
        paths can be exercised deterministically.

        Raises:
            TimeoutError: When the stub is configured with
                :attr:`StubDecisionMode.TIMEOUT` (drives rule fallback).
        """

        if self.stub_mode:
            return self._stub_decide(
                photo_set,
                excluded_dispositions=excluded_dispositions or [],
            )
        return self._live_decide(
            photo_set, item_context or {}, economics or {}, excluded_dispositions or []
        )

    def _stub_decide(
        self, photo_set: str, *, excluded_dispositions: list[str]
    ) -> DecisionResult:
        """Serve a configurable deterministic decision (STUB_MODE, task 8)."""

        config = self._active_decision_config()

        if config.mode is StubDecisionMode.TIMEOUT:
            raise TimeoutError("Stubbed LLM decision timeout")

        # Establish a deterministic baseline score + default disposition.
        fixture = PHOTO_SCORE_FIXTURES.get(photo_set)
        if fixture is not None:
            base_score = _clamp_score(fixture["expected_score"])
            default_disposition = fixture.get("drives", VALID_DISPOSITIONS[0])
        else:
            base_score = _deterministic_default_score(photo_set)
            default_disposition = VALID_DISPOSITIONS[0]

        score = config.score if config.score is not None else base_score
        reasoning = config.reasoning or "Stubbed hybrid decision reasoning."

        if config.mode is StubDecisionMode.MALFORMED:
            # Missing/invalid disposition + non-integer score payload.
            return DecisionResult(
                secondLifeScore=None,
                disposition=None,
                reasoning=reasoning,
                modelVersion=self.model_version,
                raw={"secondLifeScore": "not-an-int", "disposition": "???"},
            )

        if config.mode is StubDecisionMode.EXCLUDED:
            disposition = (
                config.disposition
                if config.disposition is not None
                else (excluded_dispositions[0] if excluded_dispositions else default_disposition)
            )
        elif config.mode is StubDecisionMode.GUARDRAIL_VIOLATING:
            disposition = config.disposition or default_disposition
        else:  # VALID
            disposition = config.disposition or default_disposition

        return DecisionResult(
            secondLifeScore=_clamp_score(score),
            disposition=disposition,
            reasoning=reasoning,
            modelVersion=self.model_version,
            raw={
                "secondLifeScore": _clamp_score(score),
                "disposition": disposition,
                "reasoning": reasoning,
            },
        )

    def _live_decide(
        self,
        photo_set: str,
        item_context: dict,
        economics: dict,
        excluded_dispositions: list[str],
    ) -> DecisionResult:
        """Structure for the real OpenAI hybrid-decision call (guarded; minimal)."""

        client = self._require_live_client()
        
        content_blocks = []
        content_blocks.append({"type": "text", "text": f"You are a routing decision engine for returns. Analyze the item photos and context.\nCategory: {item_context.get('category')}\nEconomics: {economics}\nExcluded dispositions: {excluded_dispositions}\nValid Dispositions: WAREHOUSE_RETURN, HYPERLOCAL_RESALE, GREEN_DONATION.\n\nRule 1: If condition >= 80 and div > rlc -> WAREHOUSE_RETURN\nRule 2: If condition >= 80 and weight > 10kg and rlc > div -> HYPERLOCAL_RESALE\nRule 3: If condition < 80 and rlc >= 0.5 * div -> GREEN_DONATION\nIf excluded, pick next best. \nOutput strict JSON with 'disposition' (string), 'reasoning' (string), and 'secondLifeScore' (int)."})

        if "|" in photo_set or "\\" in photo_set or "/" in photo_set:
            paths = photo_set.split("|")
            import base64
            for path in paths:
                try:
                    with open(path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                        })
                except Exception:
                    pass

        import json
        try:
            resp = client.chat.completions.create(
                model=self._settings.openai_model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": content_blocks}]
            )
            parsed = json.loads(resp.choices[0].message.content)
            
            return DecisionResult(
                secondLifeScore=parsed.get("secondLifeScore"),
                disposition=parsed.get("disposition"),
                reasoning=parsed.get("reasoning"),
                modelVersion=self.model_version,
                raw=parsed
            )
        except Exception as exc:
            raise AssessmentFailed(f"OpenAI decision failed: {str(exc)}")

    # -- live client helper ----------------------------------------------

    def _require_live_client(self):
        """Construct the live OpenAI client, requiring an API key at call time.

        Importing/constructing the SDK lazily keeps import-time free of any API
        key requirement (the demo runs in stub mode).
        """

        if not self._settings.openai_api_key:
            raise RuntimeError(
                "OPENAI API key is required for live calls; set "
                "SECONDLIFE_OPENAI_API_KEY or run in STUB_MODE."
            )
        try:  # pragma: no cover - live path not used in the demo/tests
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'openai' package is not installed; live calls are "
                "unavailable. Run in STUB_MODE."
            ) from exc
        return OpenAI(api_key=self._settings.openai_api_key)  # pragma: no cover


def get_vision_client(
    *, decision_config: StubDecisionConfig | None = None
) -> OpenAIVisionClient:
    """Return a vision client bound to the current settings (FastAPI dependency)."""

    return OpenAIVisionClient(get_settings(), decision_config=decision_config)
