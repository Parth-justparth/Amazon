"""Condition_Assessment_Service — photo upload + AI condition scoring (R2).

Implements the design "Condition_Assessment_Service" section. Exposes a FastAPI
:class:`APIRouter` plus a clean core function :func:`score_return` that the
property/unit tests drive directly with a caller-supplied session.

Endpoint: ``POST /returns/{returnRequestId}/assessment``.

Because STUB_MODE keys scoring off the return request's photo set (the item's
``photoRefs``), the HTTP endpoint accepts a JSON body describing the uploaded
files (their count + per-file ``format`` and ``sizeBytes``) plus an optional
``photoSet`` override. This lets the validations run exactly as they would for a
real multipart upload while staying deterministic and dependency-free.

Validation (in order):

* count 1-10 inclusive — zero photos → ``400 NO_PHOTOS`` (R2.4); more than 10 →
  ``400 TOO_MANY_PHOTOS`` (R2.1);
* each file format in {jpeg, png, webp} — otherwise ``415 UNSUPPORTED_FORMAT``
  (R2.5);
* each file ≤ 10 MB — otherwise ``413 FILE_TOO_LARGE`` (R2.6).

On valid input the service calls the vision client, producing an integer
``secondLifeScore`` in [0, 100] and a 1-500 char ``conditionSummary`` (R2.1,
R2.3), persists a :class:`ConditionAssessment` row, and advances the return
request to ``SCORED``. Unscorable photos yield ``422 ASSESSMENT_FAILED``
requesting clearer re-upload (R2.7).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.models import (
    ConditionAssessment,
    Item,
    ReturnRequest,
    ReturnStatus,
)
from app.domain.repository import get_session_factory
from app.integrations.openai_client import (
    AssessmentFailed,
    OpenAIVisionClient,
    ProductMismatch,
    get_vision_client,
)

#: Directory where uploaded return photos are persisted (re-read by the
#: decision engine so it sees the same images the assessment scored).
UPLOAD_DIR = Path("uploads")
#: In live mode require multiple angles for a trustworthy assessment.
MIN_LIVE_PHOTOS = 3


def uploaded_image_paths(return_request_id: str) -> list[str]:
    """Absolute paths of photos uploaded for a return request (sorted)."""

    if not UPLOAD_DIR.exists():
        return []
    return sorted(str(p.resolve()) for p in UPLOAD_DIR.glob(f"{return_request_id}_*.img"))

__all__ = [
    "router",
    "get_db",
    "PhotoDescriptor",
    "AssessmentOutcome",
    "score_return",
    "SUPPORTED_FORMATS",
    "MAX_FILE_BYTES",
    "MIN_PHOTOS",
    "MAX_PHOTOS",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Supported image formats (R2.5). Keys are normalized lower-case tokens; the
#: common MIME types map onto the same tokens.
SUPPORTED_FORMATS: frozenset[str] = frozenset({"jpeg", "png", "webp"})

#: MIME-type aliases accepted and normalized to the tokens above.
_FORMAT_ALIASES: dict[str, str] = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "image/png": "png",
    "png": "png",
    "image/webp": "webp",
    "webp": "webp",
}

#: Maximum permitted size per file in bytes (10 MB) (R2.6).
MAX_FILE_BYTES: int = 10 * 1024 * 1024

#: Inclusive photo-count bounds (R2.1, R2.4).
MIN_PHOTOS: int = 1
MAX_PHOTOS: int = 10


def _new_id(prefix: str) -> str:
    """Return a short unique id with the given prefix."""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _normalize_format(value: str) -> str | None:
    """Normalize a format/MIME token to a supported token, or None if unknown."""

    if not value:
        return None
    token = _FORMAT_ALIASES.get(value.strip().lower())
    return token


# ---------------------------------------------------------------------------
# Core data carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhotoDescriptor:
    """A single uploaded photo's validation-relevant metadata.

    Attributes:
        fmt: The declared image format or MIME type (e.g. ``"jpeg"`` /
            ``"image/png"``); normalized against :data:`SUPPORTED_FORMATS`.
        size_bytes: The file size in bytes; must be ``<= MAX_FILE_BYTES``.
    """

    fmt: str
    size_bytes: int


@dataclass
class AssessmentOutcome:
    """Outcome of :func:`score_return`.

    ``ok`` is True only when scoring succeeded and a row was persisted.
    ``status_code`` mirrors the HTTP status the router emits (200 on success).
    """

    ok: bool
    status_code: int
    assessment: ConditionAssessment | None = None
    error_code: str | None = None
    message: str | None = None
    matches_product: bool = True
    defects: tuple[str, ...] = ()
    sellable_as_new: bool = True


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_photos(
    photos: list[PhotoDescriptor],
) -> tuple[int, str | None, str | None]:
    """Validate count / format / size; return ``(status, error_code, message)``.

    ``status`` is 200 when all checks pass. Order matches the design: count
    first (R2.4, R2.1), then per-file format (R2.5), then per-file size (R2.6).
    """

    count = len(photos)
    if count < MIN_PHOTOS:
        return (
            400,
            "NO_PHOTOS",
            "At least one photo is required to assess the item's condition.",
        )
    if count > MAX_PHOTOS:
        return (
            400,
            "TOO_MANY_PHOTOS",
            f"A maximum of {MAX_PHOTOS} photos may be submitted; received {count}.",
        )

    for index, photo in enumerate(photos):
        if _normalize_format(photo.fmt) is None:
            return (
                415,
                "UNSUPPORTED_FORMAT",
                f"Photo {index + 1} has an unsupported format '{photo.fmt}'. "
                f"Supported formats: {', '.join(sorted(SUPPORTED_FORMATS))}.",
            )

    for index, photo in enumerate(photos):
        if photo.size_bytes > MAX_FILE_BYTES:
            return (
                413,
                "FILE_TOO_LARGE",
                f"Photo {index + 1} is {photo.size_bytes} bytes, which exceeds "
                f"the maximum allowed size of {MAX_FILE_BYTES} bytes (10 MB).",
            )

    return (200, None, None)


# ---------------------------------------------------------------------------
# Core scoring function (pure with respect to the supplied session)
# ---------------------------------------------------------------------------


def score_return(
    session: Session,
    returnRequestId: str,
    photo_descriptors: list[PhotoDescriptor],
    *,
    client: OpenAIVisionClient | None = None,
    photo_set_override: str | None = None,
) -> AssessmentOutcome:
    """Validate photos, score the item, persist the assessment, advance status.

    Args:
        session: An open session; the row is flushed (not committed) so callers
            own the transaction boundary.
        returnRequestId: The return request being assessed.
        photo_descriptors: Per-file metadata used for the count/format/size
            validations (R2.1, R2.4-2.6).
        client: Optional vision client; defaults to a settings-bound client
            (STUB_MODE in CI/demo).
        photo_set_override: Optional photo-set key overriding the item's
            ``photoRefs`` (used by tests to drive a specific fixture).

    Returns:
        An :class:`AssessmentOutcome`. On any rejection no assessment is
        persisted and the return request status is left unchanged.
    """

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        return AssessmentOutcome(
            ok=False,
            status_code=404,
            error_code="RETURN_NOT_FOUND",
            message=f"Return request {returnRequestId} not found.",
        )

    # 1-3. Photo count / format / size validation (R2.1, R2.4-2.6).
    status, code, message = _validate_photos(photo_descriptors)
    if status != 200:
        return AssessmentOutcome(
            ok=False, status_code=status, error_code=code, message=message
        )

    # Resolve the photo set: explicit override, else the item's first photoRef.
    item = session.get(Item, rr.itemId)
    photo_set = photo_set_override
    if photo_set is None:
        refs = (item.photoRefs if item is not None else None) or []
        photo_set = refs[0] if refs else ""

    vision = client or get_vision_client()

    item_context = {
        "category": rr.itemCategory.value,
        "title": item.title if item is not None else "",
        "productClassification": item.productClassification if item is not None else "",
    }

    # 4. Score within 30 s (STUB_MODE returns instantly). Unscorable -> R2.7;
    #    a wrong product -> PRODUCT_MISMATCH (R2 integrity).
    try:
        result = vision.assess_condition(photo_set, item_context=item_context)
    except ProductMismatch as exc:
        return AssessmentOutcome(
            ok=False,
            status_code=422,
            error_code="PRODUCT_MISMATCH",
            message=str(exc),
            matches_product=False,
        )
    except AssessmentFailed as exc:
        return AssessmentOutcome(
            ok=False,
            status_code=422,
            error_code="ASSESSMENT_FAILED",
            message=str(exc)
            or "Could not assess the item; please re-upload clearer photos.",
        )

    # 5. Persist the assessment and advance to SCORED.
    summary = result.conditionSummary
    if getattr(result, "defects", None):
        summary = (summary + " Defects: " + "; ".join(result.defects))[:500]
    assessment = ConditionAssessment(
        assessmentId=_new_id("ca"),
        returnRequestId=rr.returnRequestId,
        secondLifeScore=result.secondLifeScore,
        conditionSummary=summary,
        photoCount=len(photo_descriptors),
        modelVersion=result.modelVersion,
    )
    session.add(assessment)
    rr.status = ReturnStatus.SCORED
    session.flush()

    return AssessmentOutcome(
        ok=True,
        status_code=200,
        assessment=assessment,
        matches_product=getattr(result, "matchesProduct", True),
        defects=tuple(getattr(result, "defects", ()) or ()),
        sellable_as_new=getattr(result, "sellableAsNew", True),
    )


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["condition-assessment"])


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


class PhotoUpload(BaseModel):
    """A single uploaded photo's validation metadata (JSON upload shape)."""

    format: str = Field(..., description="Image format or MIME type.")
    sizeBytes: int = Field(..., ge=0, description="File size in bytes.")
    base64Data: str | None = Field(None, description="Base64 encoded image data (data:image/jpeg;base64,...)")


class AssessmentRequest(BaseModel):
    """POST /returns/{id}/assessment request body.

    ``photos`` mirrors a multipart upload's per-file metadata (count + format +
    size) so the count/format/size validations run identically. ``photoSet``
    optionally overrides the item's photo set (STUB_MODE fixture selection).
    """

    photos: list[PhotoUpload] = Field(default_factory=list)
    photoSet: str | None = None


def _serialize(rr: ReturnRequest, assessment: ConditionAssessment, outcome: "AssessmentOutcome") -> dict:
    """Render the assessment 200 response (incl. defects + sellable flag)."""

    return {
        "returnRequestId": rr.returnRequestId,
        "secondLifeScore": assessment.secondLifeScore,
        "conditionSummary": assessment.conditionSummary,
        "status": rr.status.value,
        "matchesProduct": outcome.matches_product,
        "defects": list(outcome.defects),
        "sellableAsNew": outcome.sellable_as_new,
    }


@router.post("/returns/{returnRequestId}/assessment")
def post_assessment(
    returnRequestId: str,
    body: AssessmentRequest,
    session: Session = Depends(get_db),
) -> dict:
    """Assess uploaded photos: verify product identity + produce the score (R2)."""

    import base64

    live = not get_settings().stub_mode

    # In live mode we need real images from multiple angles for a trustworthy
    # product-match + condition assessment.
    if live:
        with_data = [p for p in body.photos if p.base64Data]
        if len(with_data) < MIN_LIVE_PHOTOS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "MORE_PHOTOS_REQUIRED",
                    "message": f"Please upload at least {MIN_LIVE_PHOTOS} clear photos "
                    "from different angles so the item can be verified and graded.",
                },
            )

    descriptors = [
        PhotoDescriptor(fmt=p.format, size_bytes=p.sizeBytes) for p in body.photos
    ]

    # Persist uploaded photos to disk so both the assessment and the later
    # decision call analyze the exact same images.
    photo_paths: list[str] = []
    if any(p.base64Data for p in body.photos):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        # Clear any prior uploads for this return so re-assessment is clean.
        for old in UPLOAD_DIR.glob(f"{returnRequestId}_*.img"):
            try:
                old.unlink()
            except OSError:
                pass
        for i, p in enumerate(body.photos):
            if not p.base64Data:
                continue
            b64_str = p.base64Data.split(",", 1)[1] if "," in p.base64Data else p.base64Data
            try:
                img_data = base64.b64decode(b64_str)
            except Exception:
                continue
            file_path = UPLOAD_DIR / f"{returnRequestId}_{i}.img"
            with open(file_path, "wb") as f:
                f.write(img_data)
            photo_paths.append(str(file_path.resolve()))

    override_set = "|".join(photo_paths) if photo_paths else body.photoSet

    outcome = score_return(
        session,
        returnRequestId,
        descriptors,
        photo_set_override=override_set,
    )

    if outcome.ok and outcome.assessment is not None:
        rr = session.get(ReturnRequest, returnRequestId)
        return _serialize(rr, outcome.assessment, outcome)

    raise HTTPException(
        status_code=outcome.status_code,
        detail={"error": outcome.error_code, "message": outcome.message},
    )
