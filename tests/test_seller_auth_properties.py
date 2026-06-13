"""Property-based tests for Seller Authorization and A-to-z Guarantee (task 15).

Covers design Correctness Property 44 for ``app.services.return_initiation``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import (
    Base,
    Order,
    ReturnRequest,
    ReturnStatus,
    ReturnReason,
    ReturnAction,
    SellerType,
    Item,
    FlowStep,
)
from app.fixtures.loader import load_all
from app.services.return_initiation import (
    SELLER_AUTH_WINDOW_MAX_HOURS,
    authorize_seller_return,
    apply_seller_auth_timeout,
    initiate_return,
    InitiationData,
)


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


# Feature: secondlife-ai, Property 44: FBA auto-authorization versus FBM A-to-z platform refund
# Validates: Requirements 19.1, 19.2, 19.3, 19.4, 19.5
@given(
    seller_type=st.sampled_from([SellerType.FBA, SellerType.FBM]),
    fbm_action=st.sampled_from(["AUTHORIZE", "DECLINE", "TIMEOUT"]),
)
def test_property_44_seller_auth_and_atoz(seller_type, fbm_action):
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            order = session.get(Order, "ord_1001")
            order.sellerType = seller_type
            session.flush()

            now = datetime(2025, 1, 10, 12, 0, 0)
            data = InitiationData(
                orderId="ord_1001",
                itemId="item_elec_01",
                customerId=order.customerId,
                reason=ReturnReason.DEFECTIVE,
                returnAction=ReturnAction.REPLACEMENT,
                validConditionConfirmed={
                    "packaging": True,
                    "tags": True,
                    "warrantyCard": True,
                    "manuals": True,
                    "accessories": True,
                },
                damageProofProvided=True,
            )

            result = initiate_return(session, data, now=now)
            assert result.created is True
            rr = result.return_request

            if seller_type == SellerType.FBA:
                # FBA auto-authorizes onto the photo path (R19.1)
                assert rr.status == ReturnStatus.AWAITING_PHOTOS
                assert rr.flowStep == FlowStep.PROOF
                assert rr.sellerAuthDeadline is None
            else:
                # FBM opens a 24-48h seller authorization window (R19.2)
                assert rr.status == ReturnStatus.AWAITING_SELLER_AUTH
                assert rr.sellerAuthDeadline == now + timedelta(hours=SELLER_AUTH_WINDOW_MAX_HOURS)

                if fbm_action == "AUTHORIZE":
                    auth_result = authorize_seller_return(session, rr.returnRequestId, authorized=True, now=now)
                    assert auth_result.authorized is True
                    assert rr.status == ReturnStatus.AWAITING_PHOTOS
                    assert rr.sellerAuthDeadline is None
                elif fbm_action == "DECLINE":
                    auth_result = authorize_seller_return(session, rr.returnRequestId, authorized=False, now=now)
                    assert auth_result.authorized is False
                    assert auth_result.atozApplied is True
                    assert rr.atozApplied is True
                    assert auth_result.refund is not None
                elif fbm_action == "TIMEOUT":
                    timeout_time = now + timedelta(hours=SELLER_AUTH_WINDOW_MAX_HOURS, minutes=1)
                    timeout_result = apply_seller_auth_timeout(session, rr.returnRequestId, now=timeout_time)
                    assert timeout_result.authorized is False
                    assert timeout_result.atozApplied is True
                    assert rr.atozApplied is True
                    assert timeout_result.refund is not None

        finally:
            session.close()
    finally:
        engine.dispose()
