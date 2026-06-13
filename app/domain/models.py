"""Domain models: SQLAlchemy 2.x ORM entities + Pydantic v2 enums/DTOs.

This module defines the persistent schema for SecondLife AI exactly as
specified in the design "Data Models" section. Key conventions:

* **Money** is stored as integer minor units (``...Minor`` columns) alongside an
  ISO-4217 ``currency`` string. Floats are never used for money.
* **Weights** are stored as integer grams (keeps the >= 10 kg threshold exact).
* ``carbonSavingsKg`` is the only decimal value and uses :class:`~decimal.Decimal`
  via a SQL ``Numeric`` column (never a float).
* Enums are plain ``str``-valued Python enums shared between the ORM layer and
  Pydantic. They are persisted as validated strings (``native_enum=False``) so
  they map cleanly across SQLite/PostgreSQL.

A single :class:`Base` (a ``DeclarativeBase``) owns ``Base.metadata`` so the
repository/loader and tests can create all tables in one call.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.money import utc_now


# ---------------------------------------------------------------------------
# Enums (shared by ORM + Pydantic)
# ---------------------------------------------------------------------------


class Disposition(str, enum.Enum):
    """Final disposition selected for a returned item (R3.2)."""

    WAREHOUSE_RETURN = "WAREHOUSE_RETURN"
    HYPERLOCAL_RESALE = "HYPERLOCAL_RESALE"
    GREEN_DONATION = "GREEN_DONATION"
    KEEP_IT = "KEEP_IT"


class ReturnAction(str, enum.Enum):
    """Customer-selected return action, restricted to the category set (R13)."""

    REFUND = "REFUND"
    REPLACEMENT = "REPLACEMENT"
    EXCHANGE = "EXCHANGE"


class PaymentMethod(str, enum.Enum):
    """Original order payment method; drives refund timeline (R17)."""

    AMAZON_PAY_BALANCE = "AMAZON_PAY_BALANCE"
    UPI = "UPI"
    CARD = "CARD"
    NET_BANKING = "NET_BANKING"
    PAY_ON_DELIVERY = "PAY_ON_DELIVERY"


class SellerType(str, enum.Enum):
    """Fulfilment model; drives seller-authorization path (R19)."""

    FBA = "FBA"
    FBM = "FBM"


class ReturnStatus(str, enum.Enum):
    """Lifecycle status of a return request (see design state machine)."""

    INITIATING = "INITIATING"
    AWAITING_SELLER_AUTH = "AWAITING_SELLER_AUTH"
    AWAITING_PHOTOS = "AWAITING_PHOTOS"
    SCORED = "SCORED"
    AWAITING_DOA = "AWAITING_DOA"
    KEEP_IT_OFFERED = "KEEP_IT_OFFERED"
    KEEP_IT_ACCEPTED = "KEEP_IT_ACCEPTED"
    DECIDED = "DECIDED"
    WAREHOUSE = "WAREHOUSE"
    RESALE = "RESALE"
    DONATION = "DONATION"
    AWAITING_BANK_DETAILS = "AWAITING_BANK_DETAILS"
    REFUNDED = "REFUNDED"
    CLOSED = "CLOSED"
    MANUAL = "MANUAL"
    REJECTED = "REJECTED"


class FlowStep(str, enum.Enum):
    """Ordered no-skip flow step (R20.1)."""

    INITIATION = "INITIATION"
    REASON = "REASON"
    PROOF = "PROOF"
    ACTION = "ACTION"
    PICKUP_ADDRESS = "PICKUP_ADDRESS"
    INSPECTION = "INSPECTION"
    CLOSURE = "CLOSURE"


class DecisionSource(str, enum.Enum):
    """Which path produced the final disposition (R3)."""

    LLM = "LLM"
    RULE_FALLBACK = "RULE_FALLBACK"


class DoaStatus(str, enum.Enum):
    """Dead-on-arrival verification gate status (R16.3-16.6)."""

    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    SATISFIED = "SATISFIED"
    FAILED = "FAILED"


class KeepItOfferState(str, enum.Enum):
    """Keep It offer lifecycle (R11)."""

    PRESENTED = "PRESENTED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"


class RefundStatus(str, enum.Enum):
    """Refund record status (R10, R17)."""

    PENDING = "PENDING"
    WITHHELD_BANK_DETAILS = "WITHHELD_BANK_DETAILS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    MANUAL = "MANUAL"


class ListingStatus(str, enum.Enum):
    """Marketplace listing status (R6)."""

    ACTIVE = "ACTIVE"
    SOLD = "SOLD"
    EXPIRED = "EXPIRED"


class GreenPointsType(str, enum.Enum):
    """Green Points ledger entry type (R8)."""

    CREDIT_RESALE = "CREDIT_RESALE"
    CREDIT_DONATION = "CREDIT_DONATION"
    REDEEM = "REDEEM"


class InspectionOutcome(str, enum.Enum):
    """Inspection result recorded at pickup/warehouse (R20.5, R20.9)."""

    PASS = "PASS"
    FAIL = "FAIL"


class ItemCategory(str, enum.Enum):
    """Item category: the three demo categories plus the policy categories.

    Demo categories (R1.6, R3.2) drive the three reference scenarios; the
    remaining values correspond to the ``CategoryPolicy`` rows (R14, R15).
    """

    # Demo categories
    ELECTRONICS = "ELECTRONICS"
    HOME_APPLIANCES = "HOME_APPLIANCES"
    FOOTWEAR = "FOOTWEAR"
    # Policy categories (R14/R15 CategoryPolicy table)
    MOBILES_LAPTOPS_ELECTRONICS = "MOBILES_LAPTOPS_ELECTRONICS"
    CLOTHING_FOOTWEAR = "CLOTHING_FOOTWEAR"
    BOOKS = "BOOKS"
    HOME_KITCHEN_APPLIANCES = "HOME_KITCHEN_APPLIANCES"
    GROCERY_PERISHABLES = "GROCERY_PERISHABLES"
    BEAUTY_PERSONAL_CARE = "BEAUTY_PERSONAL_CARE"
    SOFTWARE_VIDEO_GAMES_MUSIC = "SOFTWARE_VIDEO_GAMES_MUSIC"


class ReturnReason(str, enum.Enum):
    """Defined list of valid return reasons (R1.2, R1.3, R11.1).

    ``MINOR_DEFECT`` and ``COLOR_APPEARANCE_NOT_AS_EXPECTED`` are the
    ``Minor_Issue_Reason`` values eligible for a Keep It offer (R11.1).
    """

    DEFECTIVE = "DEFECTIVE"
    DAMAGED_IN_TRANSIT = "DAMAGED_IN_TRANSIT"
    WRONG_ITEM = "WRONG_ITEM"
    NOT_AS_DESCRIBED = "NOT_AS_DESCRIBED"
    NO_LONGER_NEEDED = "NO_LONGER_NEEDED"
    SIZE_OR_FIT = "SIZE_OR_FIT"
    SPOILED_OR_EXPIRED = "SPOILED_OR_EXPIRED"
    # Minor_Issue_Reason values (Keep It eligible)
    MINOR_DEFECT = "MINOR_DEFECT"
    COLOR_APPEARANCE_NOT_AS_EXPECTED = "COLOR_APPEARANCE_NOT_AS_EXPECTED"


#: The subset of reasons that may trigger a Keep It offer (R11.1).
MINOR_ISSUE_REASONS: frozenset[ReturnReason] = frozenset(
    {ReturnReason.MINOR_DEFECT, ReturnReason.COLOR_APPEARANCE_NOT_AS_EXPECTED}
)

#: Reasons that indicate a damaged item and therefore require proof (R20.2).
DAMAGED_REASONS: frozenset[ReturnReason] = frozenset(
    {
        ReturnReason.DEFECTIVE,
        ReturnReason.DAMAGED_IN_TRANSIT,
        ReturnReason.MINOR_DEFECT,
    }
)


def _enum_column(enum_cls: type[enum.Enum]) -> sa.Enum:
    """Build a portable, string-valued SQL Enum type for ``enum_cls``.

    ``native_enum=False`` stores the value as a VARCHAR with a CHECK
    constraint, which maps cleanly across SQLite and PostgreSQL. The stored
    text is the enum *value* (not the member name).
    """

    return sa.Enum(
        enum_cls,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda e: [member.value for member in e],
        name=f"{enum_cls.__name__.lower()}_enum",
    )


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base owning the shared ``metadata`` for all ORM models."""


# ---------------------------------------------------------------------------
# Core catalog entities
# ---------------------------------------------------------------------------


class Order(Base):
    """A placed order. ``deliveryDate`` is the ``Return_Window_Start`` (R14.1)."""

    __tablename__ = "orders"

    orderId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    customerId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("customers.customerId"), nullable=False, index=True
    )
    deliveryDate: Mapped[datetime] = mapped_column(sa.Date, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    paymentMethod: Mapped[PaymentMethod] = mapped_column(
        _enum_column(PaymentMethod), nullable=False
    )
    sellerType: Mapped[SellerType] = mapped_column(
        _enum_column(SellerType), nullable=False
    )

    items: Mapped[list["Item"]] = relationship(back_populates="order")


class Item(Base):
    """A purchasable item within an order."""

    __tablename__ = "items"

    itemId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    orderId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("orders.orderId"), nullable=False, index=True
    )
    category: Mapped[ItemCategory] = mapped_column(
        _enum_column(ItemCategory), nullable=False
    )
    productClassification: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    isReturnable: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    largeApplianceFlag: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False
    )
    brandRequiresVerification: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False
    )
    purchasePriceMinor: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    weightGrams: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    title: Mapped[str] = mapped_column(sa.String, nullable=False, default="")
    photoRefs: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)

    order: Mapped["Order"] = relationship(back_populates="items")


class Customer(Base):
    """A customer account."""

    __tablename__ = "customers"

    customerId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String, nullable=False, default="")
    city: Mapped[str | None] = mapped_column(sa.String, nullable=True)


class City(Base):
    """A city and whether the hyperlocal marketplace serves it (R5.8, R6.1)."""

    __tablename__ = "cities"

    cityId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String, nullable=False, index=True)
    served: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    centroidLat: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    centroidLng: Mapped[float | None] = mapped_column(sa.Float, nullable=True)


# ---------------------------------------------------------------------------
# Return request + assessment + decision audit
# ---------------------------------------------------------------------------


class ReturnRequest(Base):
    """The central return request aggregate."""

    __tablename__ = "return_requests"

    returnRequestId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    orderId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("orders.orderId"), nullable=False, index=True
    )
    itemId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("items.itemId"), nullable=False, index=True
    )
    customerId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("customers.customerId"), nullable=False, index=True
    )
    reason: Mapped[ReturnReason] = mapped_column(
        _enum_column(ReturnReason), nullable=False
    )
    returnAction: Mapped[ReturnAction] = mapped_column(
        _enum_column(ReturnAction), nullable=False
    )
    status: Mapped[ReturnStatus] = mapped_column(
        _enum_column(ReturnStatus), nullable=False, default=ReturnStatus.INITIATING
    )
    flowStep: Mapped[FlowStep] = mapped_column(
        _enum_column(FlowStep), nullable=False, default=FlowStep.INITIATION
    )
    validConditionConfirmed: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    doaStatus: Mapped[DoaStatus] = mapped_column(
        _enum_column(DoaStatus), nullable=False, default=DoaStatus.NOT_REQUIRED
    )
    sellerAuthDeadline: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    atozApplied: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    pickupAddress: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    inspectionOutcome: Mapped[InspectionOutcome | None] = mapped_column(
        _enum_column(InspectionOutcome), nullable=True
    )
    carbonSavingsKg: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(10, 3, asdecimal=True), nullable=True
    )
    # --- Snapshot fields copied at creation (R1.6) ---
    itemCategory: Mapped[ItemCategory] = mapped_column(
        _enum_column(ItemCategory), nullable=False
    )
    purchasePriceMinor: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    weightGrams: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    paymentMethod: Mapped[PaymentMethod] = mapped_column(
        _enum_column(PaymentMethod), nullable=False
    )
    sellerType: Mapped[SellerType] = mapped_column(
        _enum_column(SellerType), nullable=False
    )
    returnWindowStart: Mapped[datetime] = mapped_column(sa.Date, nullable=False)
    # Dispositions excluded during re-evaluation (R5.7, R5.8, R7.7).
    excludedDispositions: Mapped[list[str]] = mapped_column(
        sa.JSON, nullable=False, default=list
    )
    createdAt: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utc_now
    )


class ConditionAssessment(Base):
    """AI condition assessment producing the SecondLife_Score (R2)."""

    __tablename__ = "condition_assessments"

    assessmentId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    returnRequestId: Mapped[str] = mapped_column(
        sa.String,
        sa.ForeignKey("return_requests.returnRequestId"),
        nullable=False,
        index=True,
    )
    secondLifeScore: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    conditionSummary: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    photoCount: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    modelVersion: Mapped[str] = mapped_column(sa.String, nullable=False, default="")
    createdAt: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        sa.CheckConstraint(
            "secondLifeScore >= 0 AND secondLifeScore <= 100",
            name="ck_assessment_score_range",
        ),
    )


class Disposition_(Base):
    """Decision audit record. Exactly one per return request (R3.6).

    Named ``Disposition_`` at the class level to avoid colliding with the
    :class:`Disposition` enum; the table is ``dispositions``.
    """

    __tablename__ = "dispositions"

    dispositionId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    returnRequestId: Mapped[str] = mapped_column(
        sa.String,
        sa.ForeignKey("return_requests.returnRequestId"),
        nullable=False,
        unique=True,  # one active decision per request (R3.6)
    )
    selected: Mapped[Disposition] = mapped_column(
        _enum_column(Disposition), nullable=False
    )
    keepItOfferState: Mapped[KeepItOfferState | None] = mapped_column(
        _enum_column(KeepItOfferState), nullable=True
    )
    partialRefundAmountMinor: Mapped[int | None] = mapped_column(
        sa.Integer, nullable=True
    )
    decisionSource: Mapped[DecisionSource] = mapped_column(
        _enum_column(DecisionSource), nullable=False
    )
    llmDisposition: Mapped[Disposition | None] = mapped_column(
        _enum_column(Disposition), nullable=True
    )
    ruleDisposition: Mapped[Disposition] = mapped_column(
        _enum_column(Disposition), nullable=False
    )
    llmReasoning: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    secondLifeScore: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    reverseLogisticsCostMinor: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    depreciatedItemValueMinor: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    weightGrams: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    itemCategory: Mapped[ItemCategory] = mapped_column(
        _enum_column(ItemCategory), nullable=False
    )
    decidedAt: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utc_now
    )


# Convenience alias matching the design entity name.
DispositionRecord = Disposition_


# ---------------------------------------------------------------------------
# Keep It offer (state folded onto Disposition audit; explicit table for clarity)
# ---------------------------------------------------------------------------


class KeepItOffer(Base):
    """Explicit Keep It offer record (R11).

    The design folds Keep It fields onto the Disposition audit record and the
    ReturnRequest; this table provides a clean place to track the offer
    lifecycle and bounded side-effects without duplicating those fields. One
    offer per return request.
    """

    __tablename__ = "keep_it_offers"

    offerId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    returnRequestId: Mapped[str] = mapped_column(
        sa.String,
        sa.ForeignKey("return_requests.returnRequestId"),
        nullable=False,
        unique=True,
    )
    state: Mapped[KeepItOfferState] = mapped_column(
        _enum_column(KeepItOfferState), nullable=False, default=KeepItOfferState.PRESENTED
    )
    partialRefundAmountMinor: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    presentedAt: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utc_now
    )
    expiresAt: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    respondedAt: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# Marketplace + charities
# ---------------------------------------------------------------------------


class MarketplaceListing(Base):
    """A resale listing visible to local buyers (R5, R6)."""

    __tablename__ = "marketplace_listings"

    listingId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    returnRequestId: Mapped[str] = mapped_column(
        sa.String,
        sa.ForeignKey("return_requests.returnRequestId"),
        nullable=False,
        index=True,
    )
    city: Mapped[str] = mapped_column(sa.String, nullable=False, index=True)
    discountedPriceMinor: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    secondLifeScore: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    photoRefs: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    status: Mapped[ListingStatus] = mapped_column(
        _enum_column(ListingStatus), nullable=False, default=ListingStatus.ACTIVE
    )
    windowStartAt: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utc_now
    )
    windowExpiresAt: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    buyerId: Mapped[str | None] = mapped_column(
        sa.String, sa.ForeignKey("customers.customerId"), nullable=True
    )
    pickupLocation: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    pickupContact: Mapped[str | None] = mapped_column(sa.String, nullable=True)


class Charity(Base):
    """A verified charity that operates donation bins / worker pickup (R7)."""

    __tablename__ = "charities"

    charityId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String, nullable=False)
    verified: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    supportsWorkerPickup: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False
    )

    bins: Mapped[list["CharityBin"]] = relationship(back_populates="charity")


class CharityBin(Base):
    """A physical donation bin with a geolocation (R7.1, R7.2)."""

    __tablename__ = "charity_bins"

    binId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    charityId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("charities.charityId"), nullable=False, index=True
    )
    city: Mapped[str] = mapped_column(sa.String, nullable=False, index=True)
    latitude: Mapped[float] = mapped_column(sa.Float, nullable=False)
    longitude: Mapped[float] = mapped_column(sa.Float, nullable=False)
    verified: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    charity: Mapped["Charity"] = relationship(back_populates="bins")


# ---------------------------------------------------------------------------
# Financial: refunds, points, balances, redemptions
# ---------------------------------------------------------------------------


class Refund(Base):
    """A refund record. At most one *successful* refund per return (R10.1)."""

    __tablename__ = "refunds"

    refundId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    returnRequestId: Mapped[str] = mapped_column(
        sa.String,
        sa.ForeignKey("return_requests.returnRequestId"),
        nullable=False,
        unique=True,  # enforces at-most-one (R10.1)
    )
    amountMinor: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    triggeringDisposition: Mapped[Disposition | None] = mapped_column(
        _enum_column(Disposition), nullable=True
    )
    paymentMethod: Mapped[PaymentMethod] = mapped_column(
        _enum_column(PaymentMethod), nullable=False
    )
    expectedCompletionWindow: Mapped[str | None] = mapped_column(
        sa.String, nullable=True
    )
    timelineStartedAt: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    atozApplied: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    status: Mapped[RefundStatus] = mapped_column(
        _enum_column(RefundStatus), nullable=False, default=RefundStatus.PENDING
    )
    attemptCount: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    createdAt: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utc_now
    )
    completedAt: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )


class GreenPointsLedger(Base):
    """Append-only Green Points ledger (R8).

    A unique constraint on ``(returnRequestId, type)`` enforces at-most-once
    crediting per return request per credit type (R8.6).
    """

    __tablename__ = "green_points_ledger"

    entryId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    customerId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("customers.customerId"), nullable=False, index=True
    )
    returnRequestId: Mapped[str | None] = mapped_column(
        sa.String, sa.ForeignKey("return_requests.returnRequestId"), nullable=True
    )
    type: Mapped[GreenPointsType] = mapped_column(
        _enum_column(GreenPointsType), nullable=False
    )
    points: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    disposition: Mapped[Disposition | None] = mapped_column(
        _enum_column(Disposition), nullable=True
    )
    createdAt: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "returnRequestId", "type", name="uq_green_points_return_type"
        ),
    )


class GreenPointsBalance(Base):
    """Per-customer Green Points balance, always a non-negative integer (R8.4)."""

    __tablename__ = "green_points_balances"

    customerId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("customers.customerId"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    __table_args__ = (
        sa.CheckConstraint("balance >= 0", name="ck_green_points_balance_nonneg"),
    )


class AmazonPayBalance(Base):
    """Per-customer Amazon Pay wallet balance in minor units (R9.2)."""

    __tablename__ = "amazon_pay_balances"

    customerId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("customers.customerId"), primary_key=True
    )
    balanceMinor: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="INR")


class RedemptionRecord(Base):
    """A points-to-Amazon-Pay redemption record (R9.6)."""

    __tablename__ = "redemption_records"

    redemptionId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    customerId: Mapped[str] = mapped_column(
        sa.String, sa.ForeignKey("customers.customerId"), nullable=False, index=True
    )
    pointsRedeemed: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    amazonPayCreditedMinor: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    conversionRate: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    completedAt: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utc_now
    )


# ---------------------------------------------------------------------------
# Policy + config + secure bank details
# ---------------------------------------------------------------------------


class CategoryPolicy(Base):
    """Category policy row driving the initiation gate (R14, R15)."""

    __tablename__ = "category_policies"

    category: Mapped[str] = mapped_column(sa.String, primary_key=True)
    windowDays: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    allowableActions: Mapped[list[str]] = mapped_column(
        sa.JSON, nullable=False, default=list
    )
    eligibilityCondition: Mapped[str] = mapped_column(sa.String, nullable=False, default="")
    returnable: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    requiresDamageProof: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False
    )


class BankDetails(Base):
    """Encrypted Pay-on-Delivery NEFT target (R18). No plaintext is persisted."""

    __tablename__ = "bank_details"

    bankDetailsId: Mapped[str] = mapped_column(sa.String, primary_key=True)
    returnRequestId: Mapped[str] = mapped_column(
        sa.String,
        sa.ForeignKey("return_requests.returnRequestId"),
        nullable=False,
        unique=True,  # one capture per request (R18)
    )
    ifscEncrypted: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    accountNumberEncrypted: Mapped[bytes] = mapped_column(
        sa.LargeBinary, nullable=False
    )
    accepted: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    createdAt: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utc_now
    )


class CO2Factor(Base):
    """Configurable carbon factor (R12.2). Decimal value, never a float."""

    __tablename__ = "co2_factors"

    factorKey: Mapped[str] = mapped_column(sa.String, primary_key=True)
    value: Mapped[Decimal] = mapped_column(
        sa.Numeric(10, 4, asdecimal=True), nullable=False
    )


# Alias matching the design entity name ``CO2_Factor``.
CO2_Factor = CO2Factor


__all__ = [
    "Base",
    # enums
    "Disposition",
    "ReturnAction",
    "PaymentMethod",
    "SellerType",
    "ReturnStatus",
    "FlowStep",
    "DecisionSource",
    "DoaStatus",
    "KeepItOfferState",
    "RefundStatus",
    "ListingStatus",
    "GreenPointsType",
    "InspectionOutcome",
    "ItemCategory",
    "ReturnReason",
    "MINOR_ISSUE_REASONS",
    "DAMAGED_REASONS",
    # entities
    "Order",
    "Item",
    "Customer",
    "City",
    "ReturnRequest",
    "ConditionAssessment",
    "Disposition_",
    "DispositionRecord",
    "KeepItOffer",
    "MarketplaceListing",
    "Charity",
    "CharityBin",
    "Refund",
    "GreenPointsLedger",
    "GreenPointsBalance",
    "AmazonPayBalance",
    "RedemptionRecord",
    "CategoryPolicy",
    "BankDetails",
    "CO2Factor",
    "CO2_Factor",
]
