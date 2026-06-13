"""Property tests for Hyperlocal_Marketplace (Task 18).

Validates Requirements 5.3, 5.5, 5.6, 6.1-6.5.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from hypothesis import given, strategies as st

from app.domain.models import Base, MarketplaceListing, ListingStatus, ReturnRequest, ReturnStatus, GreenPointsLedger, Refund
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

@given(city=st.sampled_from(["Mumbai", "Bangalore"]))
def test_marketplace_feed(city: str):
    engine, factory = _make_seeded_factory()
    try:
        app.dependency_overrides[get_db] = _override_factory(factory)
        client = TestClient(app)

        session = factory()
        try:
            from datetime import date, datetime
            # Seed a return request
            rr = ReturnRequest(
                returnRequestId="rr_mp_test",
                orderId="ord_1001",
                itemId="item_elec_01",
                customerId="cust_01",
                reason="NO_LONGER_NEEDED",
                returnAction="REFUND",
                status=ReturnStatus.RESALE,
                itemCategory="MOBILES_LAPTOPS_ELECTRONICS",
                purchasePriceMinor=129900,
                currency="INR",
                weightGrams=1200,
                paymentMethod="UPI",
                sellerType="FBA",
                returnWindowStart=date.today()
            )
            session.add(rr)
            # Seed a listing in Mumbai
            ml = MarketplaceListing(
                listingId="list_1",
                returnRequestId="rr_mp_test",
                city="Mumbai",
                discountedPriceMinor=100000,
                currency="INR",
                secondLifeScore=95,
                photoRefs=["ref1"],
                status=ListingStatus.ACTIVE,
                windowStartAt=datetime.now()
            )
            session.add(ml)
            session.commit()
        finally:
            session.close()

        res = client.get(f"/marketplace?city={city}")
        assert res.status_code == 200
        listings = res.json()["listings"]
        if city == "Mumbai":
            assert len(listings) == 1
            assert listings[0]["discountedPriceMinor"] == 100000
            assert listings[0]["status"] == "ACTIVE"
        else:
            assert len(listings) == 0
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()

def test_purchase_concurrency_and_refund():
    engine, factory = _make_seeded_factory()
    try:
        app.dependency_overrides[get_db] = _override_factory(factory)
        client = TestClient(app)

        session = factory()
        try:
            from datetime import date, datetime
            rr = ReturnRequest(
                returnRequestId="rr_mp_test",
                orderId="ord_1001",
                itemId="item_elec_01",
                customerId="cust_01",
                reason="NO_LONGER_NEEDED",
                returnAction="REFUND",
                status=ReturnStatus.RESALE,
                itemCategory="MOBILES_LAPTOPS_ELECTRONICS",
                purchasePriceMinor=129900,
                currency="INR",
                weightGrams=1200,
                paymentMethod="UPI",
                sellerType="FBA",
                returnWindowStart=date.today()
            )
            session.add(rr)
            ml = MarketplaceListing(
                listingId="list_1",
                returnRequestId="rr_mp_test",
                city="Mumbai",
                discountedPriceMinor=100000,
                currency="INR",
                secondLifeScore=95,
                photoRefs=["ref1"],
                status=ListingStatus.ACTIVE,
                windowStartAt=datetime.now()
            )
            session.add(ml)
            session.commit()
        finally:
            session.close()

        # Buyer 1 purchases
        res1 = client.post("/listings/list_1/purchase", json={"buyerId": "buyer_1"})
        assert res1.status_code == 200

        # Buyer 2 attempts purchase concurrently (actually sequential here, but tests compare-and-set)
        res2 = client.post("/listings/list_1/purchase", json={"buyerId": "buyer_2"})
        assert res2.status_code == 409

        # Buyer 1 retries idempotently
        res3 = client.post("/listings/list_1/purchase", json={"buyerId": "buyer_1"})
        assert res3.status_code == 200

        # Feed removal (R6.4)
        feed_res = client.get("/marketplace?city=Mumbai")
        assert len(feed_res.json()["listings"]) == 0

        # Check refund and green points
        session = factory()
        try:
            refund = session.query(Refund).filter_by(returnRequestId="rr_mp_test").first()
            assert refund is not None
            assert refund.status.value == "SUCCEEDED"

            points = session.query(GreenPointsLedger).filter_by(returnRequestId="rr_mp_test").first()
            assert points is not None
            assert points.points > 0
        finally:
            session.close()

    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
