"""Property tests for Return_Flow (Tasks 22 & 23).

Validates Requirement 20 (Return user flow, inspection, closure).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import Base, ReturnRequest, ReturnStatus, FlowStep, InspectionOutcome, DispositionRecord, Disposition
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

def test_return_flow_sequence():
    engine, factory = _make_seeded_factory()
    try:
        app.dependency_overrides[get_db] = _override_factory(factory)
        client = TestClient(app)

        session = factory()
        try:
            from datetime import date
            rr = ReturnRequest(
                returnRequestId="rr_flow_test",
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
                returnWindowStart=date.today(),
                flowStep=FlowStep.ACTION
            )
            session.add(rr)
            disp = DispositionRecord(
                dispositionId="disp_flow_test",
                returnRequestId="rr_flow_test",
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

        # Step: Pickup Address
        res_pickup = client.post("/returns/rr_flow_test/step/pickup", json={
            "addressLine1": "123 Test St",
            "city": "Mumbai",
            "pincode": "400001"
        })
        assert res_pickup.status_code == 200
        assert res_pickup.json()["flowStep"] == "PICKUP_ADDRESS"

        # Step: Inspection (Pass)
        res_insp = client.post("/returns/rr_flow_test/step/inspection", json={"outcome": "PASS"})
        assert res_insp.status_code == 200
        
        # Advance status to REFUNDED to allow closure
        session = factory()
        try:
            rr = session.get(ReturnRequest, "rr_flow_test")
            rr.status = ReturnStatus.REFUNDED
            session.commit()
        finally:
            session.close()

        # Step: Closure
        res_close = client.post("/returns/rr_flow_test/step/closure")
        assert res_close.status_code == 200
        assert res_close.json()["flowStep"] == "CLOSURE"
        assert res_close.json()["status"] == "CLOSED"

    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
