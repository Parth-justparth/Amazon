"""Unit tests for the Background Scheduler (Task 21)."""

import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import Base, ReturnRequest, ReturnStatus, MarketplaceListing, ListingStatus, KeepItOffer, KeepItOfferState
from app.domain.scheduler import run_scheduler_cycle
from app.fixtures.loader import load_all
from app.domain.money import utc_now

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

def test_scheduler_cycle():
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            now = utc_now()
            
            # Setup Resale expiry
            rr1 = ReturnRequest(
                returnRequestId="rr_resale_exp", orderId="ord_1001", itemId="item_elec_01",
                customerId="cust_01", reason="DEFECTIVE", returnAction="REFUND",
                status=ReturnStatus.RESALE, itemCategory="ELECTRONICS", purchasePriceMinor=100, currency="INR", weightGrams=100,
                paymentMethod="UPI", sellerType="FBA", returnWindowStart=now.date()
            )
            ml = MarketplaceListing(
                listingId="list_exp", returnRequestId="rr_resale_exp", city="Mumbai", discountedPriceMinor=50, currency="INR",
                secondLifeScore=90, status=ListingStatus.ACTIVE, windowStartAt=now - timedelta(days=3), windowExpiresAt=now - timedelta(hours=1)
            )
            session.add_all([rr1, ml])
            
            # Setup Warehouse timeout
            rr2 = ReturnRequest(
                returnRequestId="rr_wh_exp", orderId="ord_1001", itemId="item_elec_01",
                customerId="cust_01", reason="DEFECTIVE", returnAction="REFUND",
                status=ReturnStatus.WAREHOUSE, itemCategory="ELECTRONICS", purchasePriceMinor=100, currency="INR", weightGrams=100,
                paymentMethod="UPI", sellerType="FBA", returnWindowStart=now.date(), createdAt=now - timedelta(days=31)
            )
            session.add(rr2)
            
            # Setup Keep It expiry
            rr3 = ReturnRequest(
                returnRequestId="rr_ki_exp", orderId="ord_1001", itemId="item_elec_01",
                customerId="cust_01", reason="DEFECTIVE", returnAction="REFUND",
                status=ReturnStatus.KEEP_IT_OFFERED, itemCategory="ELECTRONICS", purchasePriceMinor=100, currency="INR", weightGrams=100,
                paymentMethod="UPI", sellerType="FBA", returnWindowStart=now.date()
            )
            ki = KeepItOffer(
                offerId="offer_exp", returnRequestId="rr_ki_exp", state=KeepItOfferState.PRESENTED,
                partialRefundAmountMinor=50, currency="INR", presentedAt=now - timedelta(hours=2), expiresAt=now - timedelta(minutes=30)
            )
            session.add_all([rr3, ki])
            
            # Setup FBM timeout
            rr4 = ReturnRequest(
                returnRequestId="rr_fbm_exp", orderId="ord_1001", itemId="item_elec_01",
                customerId="cust_01", reason="DEFECTIVE", returnAction="REFUND",
                status=ReturnStatus.AWAITING_SELLER_AUTH, itemCategory="ELECTRONICS", purchasePriceMinor=100, currency="INR", weightGrams=100,
                paymentMethod="UPI", sellerType="FBM", returnWindowStart=now.date(), sellerAuthDeadline=now - timedelta(hours=1)
            )
            session.add(rr4)
            
            session.commit()
        finally:
            session.close()

        # Run scheduler
        session = factory()
        try:
            stats = run_scheduler_cycle(session)
            
            assert stats["resale_expired"] == 1
            assert stats["warehouse_timed_out"] == 1
            assert stats["keep_it_expired"] == 1
            assert stats["fbm_atoz_applied"] == 1
            
            # Verify states
            rr1 = session.get(ReturnRequest, "rr_resale_exp")
            assert rr1.status != ReturnStatus.RESALE # Has been re-evaluated
            
            rr2 = session.get(ReturnRequest, "rr_wh_exp")
            assert rr2.status == ReturnStatus.MANUAL
            
            rr3 = session.get(ReturnRequest, "rr_ki_exp")
            assert rr3.status != ReturnStatus.KEEP_IT_OFFERED # Has been re-evaluated
            
            rr4 = session.get(ReturnRequest, "rr_fbm_exp")
            assert rr4.status == ReturnStatus.REFUNDED # AtoZ refund success
            
        finally:
            session.close()
    finally:
        engine.dispose()
