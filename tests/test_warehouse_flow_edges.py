"""Unit tests for Warehouse_Return_Flow (Task 17)."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import Base, Disposition, DispositionRecord, ReturnRequest, ReturnStatus
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

def test_warehouse_flow_edges():
    engine, factory = _make_seeded_factory()
    try:
        app.dependency_overrides[get_db] = _override_factory(factory)
        client = TestClient(app)

        # Setup: Return request with WAREHOUSE_RETURN disposition
        session = factory()
        try:
            from datetime import date
            rr = ReturnRequest(
                returnRequestId="rr_wh_test",
                orderId="ord_1001",
                itemId="item_elec_01",
                customerId="cust_01",
                reason="DEFECTIVE",
                returnAction="REFUND",
                status=ReturnStatus.DECIDED,
                itemCategory="MOBILES_LAPTOPS_ELECTRONICS",
                purchasePriceMinor=129900,
                currency="INR",
                weightGrams=1200,
                paymentMethod="UPI",
                sellerType="FBA",
                returnWindowStart=date.today()
            )
            session.add(rr)
            
            disp = DispositionRecord(
                dispositionId="disp_wh_test",
                returnRequestId="rr_wh_test",
                selected=Disposition.WAREHOUSE_RETURN,
                decisionSource="RULE_FALLBACK",
                ruleDisposition=Disposition.WAREHOUSE_RETURN,
                secondLifeScore=95,
                reverseLogisticsCostMinor=1000,
                depreciatedItemValueMinor=10000,
                weightGrams=1200,
                itemCategory="MOBILES_LAPTOPS_ELECTRONICS",
            )
            session.add(disp)
            session.commit()
        finally:
            session.close()

        # Generate label
        res = client.post("/returns/rr_wh_test/warehouse/label")
        assert res.status_code == 200
        assert "shippingLabelUrl" in res.json()
        assert "Approved" in res.json()["message"]

        # Assert state is WAREHOUSE
        session = factory()
        try:
            rr = session.get(ReturnRequest, "rr_wh_test")
            assert rr.status == ReturnStatus.WAREHOUSE
        finally:
            session.close()

        # Post receipt
        res = client.post("/returns/rr_wh_test/warehouse/receipt")
        assert res.status_code == 200
        assert res.json()["refundStatus"] == "SUCCEEDED"

        # Assert state is REFUNDED
        session = factory()
        try:
            rr = session.get(ReturnRequest, "rr_wh_test")
            assert rr.status == ReturnStatus.REFUNDED
        finally:
            session.close()

    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
