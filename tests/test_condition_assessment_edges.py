"""Unit tests for Condition_Assessment_Service photo-rejection edges (task 6.5).

Covers the four design example/unit cases for ``app.services.condition_assessment``:

* zero photos -> ``400 NO_PHOTOS`` (R2.4);
* unsupported file format -> ``415 UNSUPPORTED_FORMAT`` (R2.5);
* oversize file > 10 MB -> ``413 FILE_TOO_LARGE`` (R2.6);
* unscorable photos -> ``422 ASSESSMENT_FAILED`` (R2.7).

Each case asserts that no assessment is produced and the return request status
is left unchanged (still ``AWAITING_PHOTOS``). Both the core ``score_return``
function and the HTTP wrapper are exercised.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import (
    Base,
    ConditionAssessment,
    Order,
    ReturnAction,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)
from app.fixtures.loader import load_all
from app.integrations.openai_client import UNSCORABLE_PHOTO_SET
from app.main import app
from app.services.condition_assessment import (
    MAX_FILE_BYTES,
    PhotoDescriptor,
    get_db,
    score_return,
)
from app.services.return_initiation import InitiationData, initiate_return

ALL_CONFIRMED = {
    "packaging": True, "tags": True, "warrantyCard": True,
    "manuals": True, "accessories": True,
}


def _make_seeded_factory():
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


def _create_return(session) -> str:
    order = session.get(Order, "ord_1001")
    now = datetime.combine(order.deliveryDate, datetime.min.time()).replace(hour=12)
    data = InitiationData(
        orderId="ord_1001",
        itemId="item_elec_01",
        customerId=order.customerId,
        reason=ReturnReason.DEFECTIVE,
        returnAction=ReturnAction.REPLACEMENT,
        validConditionConfirmed=dict(ALL_CONFIRMED),
        damageProofProvided=True,
    )
    result = initiate_return(session, data, now=now)
    assert result.created is True
    return result.return_request.returnRequestId


def _assert_no_assessment(session, rr_id: str) -> None:
    """No assessment row persisted; the request remains AWAITING_PHOTOS."""

    rows = list(
        session.scalars(
            select(ConditionAssessment).where(
                ConditionAssessment.returnRequestId == rr_id
            )
        )
    )
    assert rows == []
    rr = session.get(ReturnRequest, rr_id)
    assert rr.status == ReturnStatus.AWAITING_PHOTOS


# ---------------------------------------------------------------------------
# Core function edges
# ---------------------------------------------------------------------------


def test_zero_photos_rejected() -> None:
    """Zero photos -> 400 NO_PHOTOS, no score produced (R2.4)."""

    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr_id = _create_return(session)
            outcome = score_return(session, rr_id, [])
            assert outcome.ok is False
            assert outcome.status_code == 400
            assert outcome.error_code == "NO_PHOTOS"
            assert outcome.assessment is None
            _assert_no_assessment(session, rr_id)
        finally:
            session.close()
    finally:
        engine.dispose()


def test_too_many_photos_rejected() -> None:
    """More than 10 photos -> 400 TOO_MANY_PHOTOS (R2.1)."""

    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr_id = _create_return(session)
            photos = [PhotoDescriptor(fmt="jpeg", size_bytes=1000) for _ in range(11)]
            outcome = score_return(session, rr_id, photos)
            assert outcome.ok is False
            assert outcome.status_code == 400
            assert outcome.error_code == "TOO_MANY_PHOTOS"
            _assert_no_assessment(session, rr_id)
        finally:
            session.close()
    finally:
        engine.dispose()


def test_unsupported_format_rejected() -> None:
    """An unsupported file format -> 415 UNSUPPORTED_FORMAT (R2.5)."""

    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr_id = _create_return(session)
            photos = [
                PhotoDescriptor(fmt="jpeg", size_bytes=1000),
                PhotoDescriptor(fmt="gif", size_bytes=1000),  # unsupported
            ]
            outcome = score_return(session, rr_id, photos)
            assert outcome.ok is False
            assert outcome.status_code == 415
            assert outcome.error_code == "UNSUPPORTED_FORMAT"
            assert "gif" in outcome.message
            _assert_no_assessment(session, rr_id)
        finally:
            session.close()
    finally:
        engine.dispose()


def test_oversize_file_rejected() -> None:
    """A file larger than 10 MB -> 413 FILE_TOO_LARGE (R2.6)."""

    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr_id = _create_return(session)
            photos = [
                PhotoDescriptor(fmt="png", size_bytes=MAX_FILE_BYTES + 1),  # oversize
            ]
            outcome = score_return(session, rr_id, photos)
            assert outcome.ok is False
            assert outcome.status_code == 413
            assert outcome.error_code == "FILE_TOO_LARGE"
            _assert_no_assessment(session, rr_id)
        finally:
            session.close()
    finally:
        engine.dispose()


def test_unscorable_photos_assessment_failed() -> None:
    """Unscorable photos -> 422 ASSESSMENT_FAILED requesting re-upload (R2.7)."""

    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr_id = _create_return(session)
            photos = [PhotoDescriptor(fmt="jpeg", size_bytes=2000)]
            outcome = score_return(
                session, rr_id, photos, photo_set_override=UNSCORABLE_PHOTO_SET
            )
            assert outcome.ok is False
            assert outcome.status_code == 422
            assert outcome.error_code == "ASSESSMENT_FAILED"
            assert "re-upload" in outcome.message.lower()
            _assert_no_assessment(session, rr_id)
        finally:
            session.close()
    finally:
        engine.dispose()


def test_valid_photos_scored_successfully() -> None:
    """A valid 1-10 photo set scores and advances the request to SCORED."""

    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr_id = _create_return(session)
            photos = [PhotoDescriptor(fmt="jpeg", size_bytes=2000)]
            outcome = score_return(session, rr_id, photos)
            assert outcome.ok is True
            assert outcome.status_code == 200
            assert outcome.assessment is not None
            assert 0 <= outcome.assessment.secondLifeScore <= 100
            rr = session.get(ReturnRequest, rr_id)
            assert rr.status == ReturnStatus.SCORED
        finally:
            session.close()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# HTTP wrapper edges (TestClient with the JSON upload shape)
# ---------------------------------------------------------------------------


def _override_factory(factory):
    def _dep():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _dep


def test_http_zero_photos_returns_400() -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr_id = _create_return(session)
            session.commit()
        finally:
            session.close()

        app.dependency_overrides[get_db] = _override_factory(factory)
        client = TestClient(app)
        try:
            res = client.post(f"/returns/{rr_id}/assessment", json={"photos": []})
            assert res.status_code == 400
            assert res.json()["detail"]["error"] == "NO_PHOTOS"
        finally:
            app.dependency_overrides.pop(get_db, None)
    finally:
        engine.dispose()


def test_http_valid_upload_scores() -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr_id = _create_return(session)
            session.commit()
        finally:
            session.close()

        app.dependency_overrides[get_db] = _override_factory(factory)
        client = TestClient(app)
        try:
            res = client.post(
                f"/returns/{rr_id}/assessment",
                json={"photos": [{"format": "jpeg", "sizeBytes": 2000}]},
            )
            assert res.status_code == 200, res.text
            payload = res.json()
            assert payload["status"] == "SCORED"
            assert 0 <= payload["secondLifeScore"] <= 100
            assert 1 <= len(payload["conditionSummary"]) <= 500
        finally:
            app.dependency_overrides.pop(get_db, None)
    finally:
        engine.dispose()
