"""Property-based tests for the Return_Initiation_Service (task 5).

Covers design Correctness Properties 1, 2, 3, 4, and 39 for
``app.services.return_initiation``. Each test is tagged with the exact
``Feature: secondlife-ai, Property {n}: {text}`` comment and a
``Validates: Requirements ...`` line, and runs against the Hypothesis ``ci``
profile (>= 100 examples; see ``tests/conftest.py``).

A fresh in-memory SQLite database is built per example (tables created from
``Base.metadata`` + the standard fixtures seeded), and the service is driven
either directly via :func:`initiate_return` (with an injected ``now`` so window
evaluation is deterministic) or over HTTP via ``TestClient`` with the session
dependency overridden.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import (
    Base,
    DecisionSource,
    Disposition,
    DispositionRecord,
    Item,
    ItemCategory,
    Order,
    ReturnAction,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)
from app.fixtures.loader import load_all
from app.main import app
from app.services.return_initiation import (
    InitiationData,
    can_generate_label,
    get_db,
    initiate_return,
)

# ---------------------------------------------------------------------------
# Fresh, seeded in-memory database per example
# ---------------------------------------------------------------------------


def _make_seeded_factory():
    """Create a disposable in-memory engine with tables + standard fixtures."""

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


# Returnable demo scenarios: (itemId, orderId, valid reason, allowable action,
# damage-proof-provided) chosen so every gate *other than* returnability and the
# return window passes — isolating the property under test.
RETURNABLE_SCENARIOS = [
    ("item_elec_01", "ord_1001", ReturnReason.DEFECTIVE, ReturnAction.REPLACEMENT, True),
    ("item_foot_01", "ord_1003", ReturnReason.SIZE_OR_FIT, ReturnAction.REFUND, False),
    ("item_appl_01", "ord_1002", ReturnReason.DEFECTIVE, ReturnAction.REPLACEMENT, True),
]

# The seeded non-returnable item (innerwear; Item_Returnability = false).
NON_RETURNABLE_SCENARIO = (
    "item_nr_01", "ord_2004", ReturnReason.WRONG_ITEM, ReturnAction.REFUND, False,
)

ALL_INITIATION_SCENARIOS = RETURNABLE_SCENARIOS + [NON_RETURNABLE_SCENARIO]

ALL_CONFIRMED = {
    "packaging": True, "tags": True, "warrantyCard": True,
    "manuals": True, "accessories": True,
}


def _delivery_date(factory, order_id: str) -> date:
    session = factory()
    try:
        order = session.get(Order, order_id)
        return order.deliveryDate
    finally:
        session.close()


def _count_returns(factory) -> int:
    session = factory()
    try:
        return len(list(session.scalars(select(ReturnRequest))))
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Property 1 — Return creation eligibility
# ---------------------------------------------------------------------------
# Feature: secondlife-ai, Property 1: Return creation eligibility
# Validates: Requirements 1.1, 1.5
@given(
    scenario=st.sampled_from(ALL_INITIATION_SCENARIOS),
    offset_days=st.integers(min_value=-2, max_value=45),
    preexisting_active=st.booleans(),
)
def test_property_1_return_creation_eligibility(
    scenario, offset_days: int, preexisting_active: bool
) -> None:
    item_id, order_id, reason, action, damage_proof = scenario
    engine, factory = _make_seeded_factory()
    try:
        delivery = _delivery_date(factory, order_id)
        now = datetime.combine(delivery + timedelta(days=offset_days), datetime.min.time())
        now = now.replace(hour=12)

        session = factory()
        try:
            item = session.get(Item, item_id)
            order = session.get(Order, order_id)
            from app.domain import policy

            returnable = policy.is_returnable(item.productClassification, item.category)
            in_window = policy.within_return_window(item.category, order.deliveryDate, now)

            # Optionally seed a pre-existing ACTIVE return for the same item.
            if preexisting_active:
                session.add(
                    ReturnRequest(
                        returnRequestId="rr_pre_active",
                        orderId=order_id,
                        itemId=item_id,
                        customerId=order.customerId,
                        reason=reason,
                        returnAction=action,
                        status=ReturnStatus.AWAITING_PHOTOS,
                        itemCategory=item.category,
                        purchasePriceMinor=item.purchasePriceMinor,
                        currency=order.currency,
                        weightGrams=item.weightGrams,
                        paymentMethod=order.paymentMethod,
                        sellerType=order.sellerType,
                        returnWindowStart=order.deliveryDate,
                    )
                )
                session.flush()

            before = len(list(session.scalars(select(ReturnRequest))))

            data = InitiationData(
                orderId=order_id,
                itemId=item_id,
                customerId=order.customerId,
                reason=reason,
                returnAction=action,
                validConditionConfirmed=dict(ALL_CONFIRMED),
                damageProofProvided=damage_proof,
            )
            result = initiate_return(session, data, now=now)

            expected_created = returnable and in_window and not preexisting_active
            assert result.created is expected_created

            after = len(list(session.scalars(select(ReturnRequest))))
            if expected_created:
                assert after == before + 1
                assert result.status_code == 201
            else:
                # Rejected with a descriptive reason; no new request created.
                assert after == before
                assert result.return_request is None
                assert result.error_code is not None
                assert result.message
        finally:
            session.close()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 2 — Exactly one valid return reason
# ---------------------------------------------------------------------------
# Reasons drawn from the defined list (valid) plus out-of-list / missing tokens.
_VALID_REASON_VALUES = [r.value for r in ReturnReason]
_reason_inputs = st.one_of(
    st.sampled_from(_VALID_REASON_VALUES),          # exactly one valid reason
    st.just(None),                                   # missing reason
    st.sampled_from(["", "BOGUS", "defective", "RETURN", "NONE"]),  # out-of-list
)


# Feature: secondlife-ai, Property 2: Exactly one valid return reason
# Validates: Requirements 1.2, 1.3
@given(reason_value=_reason_inputs)
def test_property_2_exactly_one_valid_return_reason(reason_value) -> None:
    # Use the Clothing & Footwear scenario: returnable, long (30-day) window,
    # and its eligibility condition is confirmed via Valid_Return_Condition, so
    # *any* valid reason passes every other gate — isolating reason validity.
    engine, factory = _make_seeded_factory()
    try:
        app.dependency_overrides[get_db] = _override_factory(factory)
        client = TestClient(app)

        body = {
            "orderId": "ord_1003",
            "itemId": "item_foot_01",
            "customerId": "cust_01",
            "returnAction": "REFUND",
            "validConditionConfirmed": ALL_CONFIRMED,
            "submittedAt": "2025-01-10",  # delivery date; well within 30 days
        }
        if reason_value is not None:
            body["reason"] = reason_value

        res = client.post("/returns", json=body)

        is_valid = reason_value in _VALID_REASON_VALUES
        if is_valid:
            assert res.status_code == 201, res.text
            assert res.json()["returnRequestId"]
            # Exactly one request created.
            assert _count_returns(factory) == 1
        else:
            # Missing or out-of-list reason rejected; no request created.
            assert res.status_code == 400, res.text
            assert _count_returns(factory) == 0
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


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


# ---------------------------------------------------------------------------
# Property 3 — No shipping label before disposition
# ---------------------------------------------------------------------------
# Feature: secondlife-ai, Property 3: No shipping label before disposition
# Validates: Requirements 1.4
@given(
    scenario=st.sampled_from(RETURNABLE_SCENARIOS),
    add_disposition=st.booleans(),
)
def test_property_3_no_label_before_disposition(scenario, add_disposition: bool) -> None:
    item_id, order_id, reason, action, damage_proof = scenario
    engine, factory = _make_seeded_factory()
    try:
        delivery = _delivery_date(factory, order_id)
        now = datetime.combine(delivery, datetime.min.time()).replace(hour=12)

        session = factory()
        try:
            order = session.get(Order, order_id)
            data = InitiationData(
                orderId=order_id,
                itemId=item_id,
                customerId=order.customerId,
                reason=reason,
                returnAction=action,
                validConditionConfirmed=dict(ALL_CONFIRMED),
                damageProofProvided=damage_proof,
            )
            result = initiate_return(session, data, now=now)
            assert result.created is True
            rr = result.return_request

            # No disposition yet -> label generation must be refused (R1.4).
            assert can_generate_label(session, rr) is False

            if add_disposition:
                # Once a Disposition exists, the guard permits a label.
                session.add(
                    DispositionRecord(
                        dispositionId="disp_test",
                        returnRequestId=rr.returnRequestId,
                        selected=Disposition.WAREHOUSE_RETURN,
                        decisionSource=DecisionSource.RULE_FALLBACK,
                        ruleDisposition=Disposition.WAREHOUSE_RETURN,
                        secondLifeScore=90,
                        reverseLogisticsCostMinor=18000,
                        depreciatedItemValueMinor=91000,
                        weightGrams=rr.weightGrams,
                        itemCategory=rr.itemCategory,
                    )
                )
                session.flush()
                assert can_generate_label(session, rr) is True
        finally:
            session.close()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 4 — Item snapshot fidelity
# ---------------------------------------------------------------------------
# Feature: secondlife-ai, Property 4: Item snapshot fidelity
# Validates: Requirements 1.6
@given(scenario=st.sampled_from(RETURNABLE_SCENARIOS))
def test_property_4_item_snapshot_fidelity(scenario) -> None:
    item_id, order_id, reason, action, damage_proof = scenario
    engine, factory = _make_seeded_factory()
    try:
        delivery = _delivery_date(factory, order_id)
        now = datetime.combine(delivery, datetime.min.time()).replace(hour=12)

        session = factory()
        try:
            item = session.get(Item, item_id)
            order = session.get(Order, order_id)
            data = InitiationData(
                orderId=order_id,
                itemId=item_id,
                customerId=order.customerId,
                reason=reason,
                returnAction=action,
                validConditionConfirmed=dict(ALL_CONFIRMED),
                damageProofProvided=damage_proof,
            )
            result = initiate_return(session, data, now=now)
            assert result.created is True
            rr = result.return_request

            # Recorded snapshot equals the source catalog item / order values.
            assert rr.itemCategory == item.category
            assert rr.purchasePriceMinor == item.purchasePriceMinor
            assert rr.currency == order.currency
            # Plus the remaining snapshot fields the design records (R1.6).
            assert rr.weightGrams == item.weightGrams
            assert rr.paymentMethod == order.paymentMethod
            assert rr.sellerType == order.sellerType
            assert rr.returnWindowStart == order.deliveryDate
        finally:
            session.close()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 39 — Valid return condition confirmation
# ---------------------------------------------------------------------------
# Feature: secondlife-ai, Property 39: Valid return condition confirmation
# Validates: Requirements 16.1, 16.2
@given(
    packaging=st.booleans(),
    tags=st.booleans(),
    warrantyCard=st.booleans(),
    manuals=st.booleans(),
    accessories=st.booleans(),
)
def test_property_39_valid_return_condition_confirmation(
    packaging: bool, tags: bool, warrantyCard: bool, manuals: bool, accessories: bool
) -> None:
    # Clothing & Footwear scenario: returnable, in-window, valid reason/action,
    # so the only remaining gate is the Valid_Return_Condition confirmation.
    item_id, order_id = "item_foot_01", "ord_1003"
    engine, factory = _make_seeded_factory()
    try:
        delivery = _delivery_date(factory, order_id)
        now = datetime.combine(delivery, datetime.min.time()).replace(hour=12)
        confirmation = {
            "packaging": packaging, "tags": tags, "warrantyCard": warrantyCard,
            "manuals": manuals, "accessories": accessories,
        }
        all_true = all(confirmation.values())

        session = factory()
        try:
            order = session.get(Order, order_id)
            before = len(list(session.scalars(select(ReturnRequest))))
            data = InitiationData(
                orderId=order_id,
                itemId=item_id,
                customerId=order.customerId,
                reason=ReturnReason.SIZE_OR_FIT,
                returnAction=ReturnAction.REFUND,
                validConditionConfirmed=confirmation,
                damageProofProvided=False,
            )
            result = initiate_return(session, data, now=now)
            after = len(list(session.scalars(select(ReturnRequest))))

            if all_true:
                assert result.created is True
                assert after == before + 1
            else:
                assert result.created is False
                assert result.error_code == "INVALID_CONDITION"
                assert after == before
                # The message names each unconfirmed element (R16.2).
                expected_unconfirmed = [
                    k for k in ("packaging", "tags", "warrantyCard", "manuals", "accessories")
                    if not confirmation[k]
                ]
                assert result.unconfirmed == expected_unconfirmed
                for element in expected_unconfirmed:
                    assert element in result.message
        finally:
            session.close()
    finally:
        engine.dispose()
