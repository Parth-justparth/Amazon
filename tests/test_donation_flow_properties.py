"""Property tests for Green_Donation_Flow (Task 20).

Validates Properties 18 and 19 (Requirements 7).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from hypothesis import given, strategies as st

from app.domain.models import Base, ReturnRequest, ReturnStatus, DispositionRecord, Disposition, Customer, City, Charity, CharityBin, GreenPointsLedger, Refund
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
        # Add custom seed for this test
        cust = session.get(Customer, "cust_01")
        if cust:
            cust.city = "TestCity"
        
        city = City(cityId="city_test", name="TestCity", served=True, centroidLat=10.0, centroidLng=10.0)
        session.add(city)
        
        charity = Charity(charityId="char_test", name="Test Charity", verified=True, supportsWorkerPickup=True)
        session.add(charity)
        
        # Bin within 25km (approx 10km away)
        bin1 = CharityBin(binId="bin_1", charityId="char_test", city="TestCity", latitude=10.09, longitude=10.0, verified=True)
        session.add(bin1)
        
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

def test_donation_options():
    engine, factory = _make_seeded_factory()
    try:
        app.dependency_overrides[get_db] = _override_factory(factory)
        client = TestClient(app)

        session = factory()
        try:
            from datetime import date
            rr = ReturnRequest(
                returnRequestId="rr_don_test",
                orderId="ord_1001",
                itemId="item_foot_01",
                customerId="cust_01",
                reason="SIZE_OR_FIT",
                returnAction="REFUND",
                status=ReturnStatus.DECIDED,
                itemCategory="CLOTHING_FOOTWEAR",
                purchasePriceMinor=50000,
                currency="INR",
                weightGrams=500,
                paymentMethod="UPI",
                sellerType="FBA",
                returnWindowStart=date.today()
            )
            session.add(rr)
            disp = DispositionRecord(
                dispositionId="disp_don_test",
                returnRequestId="rr_don_test",
                selected=Disposition.GREEN_DONATION,
                decisionSource="RULE_FALLBACK",
                ruleDisposition=Disposition.GREEN_DONATION,
                secondLifeScore=40,
                reverseLogisticsCostMinor=5000,
                depreciatedItemValueMinor=2000,
                weightGrams=500,
                itemCategory="CLOTHING_FOOTWEAR",
            )
            session.add(disp)
            session.commit()
        finally:
            session.close()

        res = client.get("/returns/rr_don_test/donation/options")
        assert res.status_code == 200
        
        data = res.json()
        assert data["pickupAvailable"] is True
        assert data["nearestBin"] is not None
        assert data["nearestBin"]["binId"] == "bin_1"
        assert data["nearestBin"]["distanceKm"] <= 25.0

        # Schedule pickup
        res_sch = client.post("/returns/rr_don_test/donation/pickup", json={"charityId": "char_test"})
        assert res_sch.status_code == 200
        assert "scheduledDate" in res_sch.json()

        # Confirm donation
        res_conf = client.post("/returns/rr_don_test/donation/confirm")
        assert res_conf.status_code == 200
        assert "Refund and Green Points issued" in res_conf.json()["message"]

        # Assert DB states
        session = factory()
        try:
            rr = session.get(ReturnRequest, "rr_don_test")
            assert rr.status == ReturnStatus.REFUNDED
            
            refund = session.query(Refund).filter_by(returnRequestId="rr_don_test").first()
            assert refund is not None
            assert refund.status.value == "SUCCEEDED"

            points = session.query(GreenPointsLedger).filter_by(returnRequestId="rr_don_test").first()
            assert points is not None
        finally:
            session.close()

    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
