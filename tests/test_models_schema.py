"""Schema build + smoke tests for the ORM models and repository helpers.

These tests confirm that the full ``Base.metadata`` builds with no mapper or
constraint errors, that every entity can be instantiated and persisted, and
that the atomic repository helpers behave as specified (single-winner claim,
idempotent refund/credit, atomic redemption).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain import repository as repo
from app.domain.models import (
    AmazonPayBalance,
    Base,
    BankDetails,
    CategoryPolicy,
    Charity,
    CharityBin,
    City,
    CO2Factor,
    ConditionAssessment,
    Customer,
    Disposition,
    Disposition_,
    DecisionSource,
    GreenPointsBalance,
    GreenPointsType,
    Item,
    ItemCategory,
    KeepItOffer,
    ListingStatus,
    MarketplaceListing,
    Order,
    PaymentMethod,
    RedemptionRecord,
    Refund,
    ReturnAction,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
    SellerType,
)


@pytest.fixture
def session() -> Session:
    """A fresh in-memory SQLite session with all tables created."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_metadata_builds_all_tables() -> None:
    """Creating all tables on a clean engine raises no mapper/DDL errors."""

    engine = create_engine("sqlite://", poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    table_names = set(sa.inspect(engine).get_table_names())
    # A representative sample of required tables must exist.
    expected = {
        "orders",
        "items",
        "customers",
        "return_requests",
        "condition_assessments",
        "dispositions",
        "keep_it_offers",
        "marketplace_listings",
        "charities",
        "charity_bins",
        "refunds",
        "green_points_ledger",
        "green_points_balances",
        "amazon_pay_balances",
        "redemption_records",
        "category_policies",
        "bank_details",
        "co2_factors",
        "cities",
    }
    assert expected <= table_names
    engine.dispose()


def _seed_basic(session: Session) -> ReturnRequest:
    """Insert a minimal customer/order/item/return-request graph."""

    session.add(Customer(customerId="cust_01", name="Aarav", city="Bengaluru"))
    session.add(
        Order(
            orderId="ord_1001",
            customerId="cust_01",
            deliveryDate=date(2025, 1, 12),
            currency="INR",
            paymentMethod=PaymentMethod.UPI,
            sellerType=SellerType.FBA,
        )
    )
    session.add(
        Item(
            itemId="item_elec_01",
            orderId="ord_1001",
            category=ItemCategory.ELECTRONICS,
            productClassification="HEADPHONES",
            isReturnable=True,
            purchasePriceMinor=1_299_900,
            currency="INR",
            weightGrams=280,
            title="Noise-Cancelling Headphones",
            photoRefs=["photos_elec_pristine"],
        )
    )
    rr = ReturnRequest(
        returnRequestId="rr_5001",
        orderId="ord_1001",
        itemId="item_elec_01",
        customerId="cust_01",
        reason=ReturnReason.MINOR_DEFECT,
        returnAction=ReturnAction.REPLACEMENT,
        status=ReturnStatus.AWAITING_PHOTOS,
        validConditionConfirmed={"packaging": True, "tags": True},
        itemCategory=ItemCategory.ELECTRONICS,
        purchasePriceMinor=1_299_900,
        currency="INR",
        weightGrams=280,
        paymentMethod=PaymentMethod.UPI,
        sellerType=SellerType.FBA,
        returnWindowStart=date(2025, 1, 12),
        excludedDispositions=[],
    )
    session.add(rr)
    session.commit()
    return rr


def test_instantiate_and_persist_all_entities(session: Session) -> None:
    """Every entity persists and reads back, exercising enums/JSON/Numeric."""

    _seed_basic(session)

    session.add(
        ConditionAssessment(
            assessmentId="as_1",
            returnRequestId="rr_5001",
            secondLifeScore=92,
            conditionSummary="Pristine",
            photoCount=3,
            modelVersion="gpt-4o-2024-08-06",
        )
    )
    session.add(
        Disposition_(
            dispositionId="dp_1",
            returnRequestId="rr_5001",
            selected=Disposition.WAREHOUSE_RETURN,
            decisionSource=DecisionSource.LLM,
            llmDisposition=Disposition.WAREHOUSE_RETURN,
            ruleDisposition=Disposition.WAREHOUSE_RETURN,
            llmReasoning="value far exceeds cost",
            secondLifeScore=92,
            reverseLogisticsCostMinor=18000,
            depreciatedItemValueMinor=91000,
            weightGrams=280,
            itemCategory=ItemCategory.ELECTRONICS,
        )
    )
    session.add(
        KeepItOffer(
            offerId="ko_1",
            returnRequestId="rr_5001",
            partialRefundAmountMinor=11_697,
            currency="INR",
        )
    )
    session.add(
        MarketplaceListing(
            listingId="ls_1",
            returnRequestId="rr_5001",
            city="Bengaluru",
            discountedPriceMinor=900_000,
            currency="INR",
            secondLifeScore=85,
            photoRefs=["a", "b"],
            status=ListingStatus.ACTIVE,
        )
    )
    session.add(Charity(charityId="char_01", name="GreenEarth", verified=True, supportsWorkerPickup=True))
    session.add(
        CharityBin(
            binId="bin_blr_01",
            charityId="char_01",
            city="Bengaluru",
            latitude=12.9716,
            longitude=77.5946,
            verified=True,
        )
    )
    session.add(GreenPointsBalance(customerId="cust_01", balance=0))
    session.add(AmazonPayBalance(customerId="cust_01", balanceMinor=0, currency="INR"))
    session.add(
        RedemptionRecord(
            redemptionId="rd_1",
            customerId="cust_01",
            pointsRedeemed=500,
            amazonPayCreditedMinor=50000,
            conversionRate=100,
        )
    )
    session.add(
        CategoryPolicy(
            category="Clothing & Footwear",
            windowDays=30,
            allowableActions=["REFUND", "EXCHANGE"],
            eligibilityCondition="UNWORN_UNWASHED_TAGS",
            returnable=True,
            requiresDamageProof=False,
        )
    )
    session.add(
        BankDetails(
            bankDetailsId="bd_1",
            returnRequestId="rr_5001",
            ifscEncrypted=b"x",
            accountNumberEncrypted=b"y",
            accepted=True,
        )
    )
    session.add(CO2Factor(factorKey="per_km", value=Decimal("0.12")))
    session.add(City(cityId="city_blr", name="Bengaluru", served=True))
    session.commit()

    # Read back a Numeric/Decimal column and an enum column.
    rr = session.get(ReturnRequest, "rr_5001")
    rr.carbonSavingsKg = Decimal("7.225")
    session.commit()
    assert session.get(ReturnRequest, "rr_5001").carbonSavingsKg == Decimal("7.225")
    assert session.get(Disposition_, "dp_1").selected is Disposition.WAREHOUSE_RETURN


def test_claim_listing_single_winner(session: Session) -> None:
    """Two claims on one ACTIVE listing yield exactly one winner (R6.5)."""

    _seed_basic(session)
    session.add(
        MarketplaceListing(
            listingId="ls_1",
            returnRequestId="rr_5001",
            city="Bengaluru",
            discountedPriceMinor=900_000,
            currency="INR",
            secondLifeScore=85,
            photoRefs=[],
            status=ListingStatus.ACTIVE,
        )
    )
    session.commit()

    first = repo.claim_listing(session, "ls_1", "buyer_a")
    second = repo.claim_listing(session, "ls_1", "buyer_b")
    session.commit()

    assert first.claimed is True
    assert second.claimed is False
    listing = session.get(MarketplaceListing, "ls_1")
    assert listing.status is ListingStatus.SOLD
    assert listing.buyerId == "buyer_a"


def test_issue_refund_once_is_idempotent(session: Session) -> None:
    """At most one refund per return request (R10.1)."""

    _seed_basic(session)
    r1 = repo.issue_refund_once(
        session, "rr_5001", 1_299_900, "INR", PaymentMethod.UPI, Disposition.WAREHOUSE_RETURN
    )
    r2 = repo.issue_refund_once(
        session, "rr_5001", 1_299_900, "INR", PaymentMethod.UPI, Disposition.WAREHOUSE_RETURN
    )
    session.commit()

    assert r1.created is True
    assert r2.created is False
    assert r1.refundId == r2.refundId
    count = session.scalar(
        sa.select(sa.func.count()).select_from(Refund).where(Refund.returnRequestId == "rr_5001")
    )
    assert count == 1


def test_credit_green_points_once_is_idempotent(session: Session) -> None:
    """Credit at most once per (returnRequestId, type) and update balance (R8.6)."""

    _seed_basic(session)
    c1 = repo.credit_green_points_once(
        session, "cust_01", "rr_5001", GreenPointsType.CREDIT_DONATION, 300, Disposition.GREEN_DONATION
    )
    c2 = repo.credit_green_points_once(
        session, "cust_01", "rr_5001", GreenPointsType.CREDIT_DONATION, 300, Disposition.GREEN_DONATION
    )
    session.commit()

    assert c1.credited is True
    assert c2.credited is False
    assert session.get(GreenPointsBalance, "cust_01").balance == 300


def test_redeem_points_atomic(session: Session) -> None:
    """Redemption deducts points and credits Amazon Pay atomically (R9.2)."""

    _seed_basic(session)
    repo.credit_green_points_once(
        session, "cust_01", "rr_5001", GreenPointsType.CREDIT_RESALE, 500, Disposition.HYPERLOCAL_RESALE
    )
    session.commit()

    result = repo.redeem_points(session, "cust_01", 500, conversion_rate_minor_per_point=100)
    session.commit()

    assert result.redeemed is True
    assert result.amazonPayCreditedMinor == 50000
    assert result.newBalance == 0
    assert session.get(AmazonPayBalance, "cust_01").balanceMinor == 50000


def test_redeem_points_rejects_over_balance(session: Session) -> None:
    """Over-balance redemption is rejected with no state change (R9.3)."""

    _seed_basic(session)
    session.add(GreenPointsBalance(customerId="cust_01", balance=100))
    session.commit()

    result = repo.redeem_points(session, "cust_01", 500, conversion_rate_minor_per_point=100)
    session.commit()

    assert result.redeemed is False
    assert "insufficient" in (result.reason or "")
    assert session.get(GreenPointsBalance, "cust_01").balance == 100
    assert session.get(AmazonPayBalance, "cust_01") is None
