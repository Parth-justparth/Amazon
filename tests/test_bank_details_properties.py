"""Property-based tests for the Bank_Details_Capture_Service (task 12.2).

Covers design Correctness Property 43 for ``app.services.bank_details`` and
Requirements 18.1-18.5. Each test is tagged with the exact
``Feature: secondlife-ai, Property 43: {text}`` comment and a
``Validates: Requirements ...`` line, and runs against the Hypothesis ``ci``
profile (>= 100 examples; see ``tests/conftest.py``).

Each example builds a fresh in-memory SQLite database (tables + standard
fixtures) and creates a Pay-on-Delivery return request whose
``returnRequestId`` satisfies the ``BankDetails`` foreign key, then exercises
``capture_bank_details`` directly — no mocks, no network. The real
``app.domain.crypto`` Fernet round-trip is used so the encrypt/decrypt and
no-plaintext-leak assertions reflect production behaviour.
"""

from __future__ import annotations

import string
import uuid

from hypothesis import assume, given
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.crypto import decrypt
from app.domain.models import (
    BankDetails,
    Base,
    Item,
    Order,
    PaymentMethod,
    ReturnAction,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)
from app.fixtures.loader import load_all
from app.services.bank_details import (
    ACCOUNT_NUMBER_PATTERN,
    IFSC_PATTERN,
    capture_bank_details,
    validate_account_number,
    validate_ifsc,
)

# Alphabet of valid IFSC / account characters.
_ALNUM = string.ascii_letters + string.digits
_DIGITS = string.digits


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


def _new_pod_rr(session) -> ReturnRequest:
    """Create a Pay-on-Delivery return request referencing seeded order/item."""

    item = session.get(Item, "item_foot_01")
    order = session.get(Order, "ord_1003")
    rr = ReturnRequest(
        returnRequestId=f"rr_{uuid.uuid4().hex[:10]}",
        orderId=order.orderId,
        itemId=item.itemId,
        customerId=order.customerId,
        reason=ReturnReason.DEFECTIVE,
        returnAction=ReturnAction.REFUND,
        status=ReturnStatus.AWAITING_BANK_DETAILS,
        itemCategory=item.category,
        purchasePriceMinor=item.purchasePriceMinor,
        currency=item.currency,
        weightGrams=item.weightGrams,
        paymentMethod=PaymentMethod.PAY_ON_DELIVERY,
        sellerType=order.sellerType,
        returnWindowStart=order.deliveryDate,
        excludedDispositions=[],
    )
    session.add(rr)
    session.flush()
    return rr


def _bank_row(session, return_request_id: str) -> BankDetails | None:
    return session.scalar(
        select(BankDetails).where(
            BankDetails.returnRequestId == return_request_id
        )
    )


# Strategies for valid inputs.
_valid_ifsc = st.text(alphabet=_ALNUM, min_size=11, max_size=11)
_valid_account = st.text(alphabet=_DIGITS, min_size=9, max_size=18)


# ===========================================================================
# Property 43 — valid pair is accepted, encrypted, and round-trips
# ===========================================================================
# Feature: secondlife-ai, Property 43: Bank-details validation
# Validates: Requirements 18.1, 18.2, 18.5
@given(ifsc=_valid_ifsc, account=_valid_account)
def test_property_43_valid_pair_accepted_and_encrypted(ifsc, account) -> None:
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_pod_rr(session)
            result = capture_bank_details(
                session, rr.returnRequestId, ifsc, account
            )

            # Accepted with an opaque bankDetailsId reference (R18.5).
            assert result.accepted is True
            assert result.bankDetailsId is not None
            assert result.error_code is None and result.field is None

            # A single encrypted row is persisted (R18.2).
            row = _bank_row(session, rr.returnRequestId)
            assert row is not None
            assert row.bankDetailsId == result.bankDetailsId
            assert row.accepted is True

            # Stored values are encrypted-at-rest and round-trip to plaintext.
            assert decrypt(row.ifscEncrypted) == ifsc
            assert decrypt(row.accountNumberEncrypted) == account

            # No-plaintext-leak: ciphertext must not contain the raw input.
            assert ifsc.encode("utf-8") not in row.ifscEncrypted
            assert account.encode("utf-8") not in row.accountNumberEncrypted
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 43 — invalid IFSC is rejected, storing nothing
# ===========================================================================
# Feature: secondlife-ai, Property 43: Bank-details validation
# Validates: Requirements 18.1, 18.3
@given(
    ifsc=st.text(min_size=0, max_size=20),
    account=st.text(alphabet=_DIGITS, min_size=9, max_size=18),
)
def test_property_43_invalid_ifsc_rejected_stores_nothing(ifsc, account) -> None:
    # Only exercise IFSC values that genuinely fail validation.
    assume(not validate_ifsc(ifsc))
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_pod_rr(session)
            result = capture_bank_details(
                session, rr.returnRequestId, ifsc, account
            )

            # Rejected naming the ifsc field + expected 11-char format (R18.3).
            assert result.accepted is False
            assert result.field == "ifsc"
            assert result.error_code == "IFSC_INVALID"
            assert result.bankDetailsId is None
            assert result.message and "11" in result.message
            assert "ifsc" in result.message.lower()

            # Nothing stored.
            assert _bank_row(session, rr.returnRequestId) is None
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 43 — invalid account number is rejected, storing nothing
# ===========================================================================
# Feature: secondlife-ai, Property 43: Bank-details validation
# Validates: Requirements 18.1, 18.4
@given(
    ifsc=_valid_ifsc,  # valid IFSC so account validation is reached (IFSC first)
    account=st.text(min_size=0, max_size=25),
)
def test_property_43_invalid_account_rejected_stores_nothing(ifsc, account) -> None:
    # Only exercise account values that genuinely fail validation.
    assume(not validate_account_number(account))
    engine, factory = _make_seeded_factory()
    try:
        session = factory()
        try:
            rr = _new_pod_rr(session)
            result = capture_bank_details(
                session, rr.returnRequestId, ifsc, account
            )

            # Rejected naming accountNumber + expected 9-18 digit format (R18.4).
            assert result.accepted is False
            assert result.field == "accountNumber"
            assert result.error_code == "ACCOUNT_INVALID"
            assert result.bankDetailsId is None
            assert result.message and "accountNumber" in result.message
            assert "9" in result.message and "18" in result.message

            # Nothing stored.
            assert _bank_row(session, rr.returnRequestId) is None
        finally:
            session.close()
    finally:
        engine.dispose()


# ===========================================================================
# Property 43 — validators agree with their regex patterns (pure functions)
# ===========================================================================
# Feature: secondlife-ai, Property 43: Bank-details validation
# Validates: Requirements 18.1, 18.3, 18.4
@given(value=st.text(min_size=0, max_size=25))
def test_property_43_validators_match_patterns(value) -> None:
    # validate_ifsc accepts iff the value is exactly 11 ASCII alnum chars.
    assert validate_ifsc(value) == (IFSC_PATTERN.fullmatch(value) is not None)
    # validate_account_number accepts iff the value is 9-18 ASCII digits.
    assert validate_account_number(value) == (
        ACCOUNT_NUMBER_PATTERN.fullmatch(value) is not None
    )
