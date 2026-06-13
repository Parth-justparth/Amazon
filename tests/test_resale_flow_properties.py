"""Property tests for Hyperlocal_Resale_Flow (Task 19).

Validates Property 13 (Requirements 5.1, 5.2).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from hypothesis import given, strategies as st

from app.domain.models import Base, MarketplaceListing, ReturnRequest, ReturnStatus, DispositionRecord, Disposition
from app.fixtures.loader import load_all
from app.main import app
from app.services.return_initiation import get_db

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

@given(score=st.integers(min_value=80, max_value=100))
def test_property_13_resale_listing_constraints(score: int):
    engine, factory = _make_seeded_factory()
    try:
        app.dependency_overrides[get_db] = _override_factory(factory)
        client = TestClient(app)

        session = factory()
        try:
            from datetime import date
            rr = ReturnRequest(
                returnRequestId="rr_resale_test",
                orderId="ord_1001",
                itemId="item_elec_01",
                customerId="cust_01",
                reason="NO_LONGER_NEEDED",
                returnAction="REFUND",
                status=ReturnStatus.DECIDED,
                itemCategory="MOBILES_LAPTOPS_ELECTRONICS",
                purchasePriceMinor=100000,
                currency="INR",
                weightGrams=15000,
                paymentMethod="UPI",
                sellerType="FBA",
                returnWindowStart=date.today(),
                pickupAddress={"addressLine1": "Test Addr", "city": "Mumbai", "pincode": "400001"}
            )
            session.add(rr)
            disp = DispositionRecord(
                dispositionId="disp_resale_test",
                returnRequestId="rr_resale_test",
                selected=Disposition.HYPERLOCAL_RESALE,
                decisionSource="RULE_FALLBACK",
                ruleDisposition=Disposition.HYPERLOCAL_RESALE,
                secondLifeScore=score,
                reverseLogisticsCostMinor=5000,
                depreciatedItemValueMinor=80000,
                weightGrams=15000,
                itemCategory="MOBILES_LAPTOPS_ELECTRONICS",
            )
            session.add(disp)
            session.commit()
        finally:
            session.close()

        res = client.post("/returns/rr_resale_test/resale/list")
        assert res.status_code == 200
        
        data = res.json()
        assert "keep it at home for the 48-hour window" in data["message"]
        
        # Original price was 100000. It should be strictly less.
        assert data["discountedPriceMinor"] < 100000
        
        # Verify db state
        session = factory()
        try:
            ml = session.query(MarketplaceListing).filter_by(returnRequestId="rr_resale_test").first()
            assert ml is not None
            assert ml.status.value == "ACTIVE"
            rr = session.get(ReturnRequest, "rr_resale_test")
            assert rr.status.value == "RESALE"
        finally:
            session.close()

    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
