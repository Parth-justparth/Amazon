"""Idempotent seed/fixture loader.

Translates the declarative datasets in :mod:`app.fixtures.seed_data` into ORM
rows and populates the database. The loader is **idempotent**: every row is
keyed by its primary key and skipped if it already exists, so calling
:func:`load_all` repeatedly never raises or duplicates rows.

Typical usage at app startup::

    from app.fixtures.loader import seed_on_startup
    seed_on_startup()  # init_db() + load_all() inside a session scope

or against a caller-managed session (tests)::

    from app.domain.repository import init_db, get_session_factory, session_scope
    init_db()
    with session_scope() as session:
        summary = load_all(session)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.models import (
    AmazonPayBalance,
    CategoryPolicy,
    Charity,
    CharityBin,
    City,
    CO2Factor,
    Customer,
    FlowStep,
    GreenPointsBalance,
    Item,
    ListingStatus,
    MarketplaceListing,
    Order,
    ReturnRequest,
    ReturnStatus,
)
from app.domain.money import utc_now
from app.domain.repository import init_db, session_scope
from app.fixtures import seed_data as data


@dataclass
class LoadSummary:
    """Per-dataset count of rows present after a :func:`load_all` call.

    Counts reflect *ensured* rows (existing + newly inserted), so they are
    stable across repeated idempotent loads.
    """

    customers: int = 0
    orders: int = 0
    items: int = 0
    charities: int = 0
    charity_bins: int = 0
    cities: int = 0
    category_policies: int = 0
    co2_factors: int = 0
    green_points_balances: int = 0
    amazon_pay_balances: int = 0
    inserted: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, int]:
        """Return the per-dataset ensured-row counts as a plain dict."""

        return {
            "customers": self.customers,
            "orders": self.orders,
            "items": self.items,
            "charities": self.charities,
            "charity_bins": self.charity_bins,
            "cities": self.cities,
            "category_policies": self.category_policies,
            "co2_factors": self.co2_factors,
            "green_points_balances": self.green_points_balances,
            "amazon_pay_balances": self.amazon_pay_balances,
        }


def _ensure(session: Session, model: type, pk: str, values: dict) -> bool:
    """Insert ``model(**values)`` if no row with primary key ``pk`` exists.

    Returns ``True`` when a new row was inserted, ``False`` when skipped. The
    primary-key lookup makes the loader safe to call repeatedly (idempotent).
    """

    if session.get(model, pk) is not None:
        return False
    session.add(model(**values))
    session.flush()
    return True


def load_all(
    session: Session,
    *,
    include_missing_co2_factor_toggle: bool = False,
) -> LoadSummary:
    """Populate the database with all seed datasets (idempotently).

    Rows are inserted in foreign-key-safe order: customers → orders → items,
    charities → bins, then cities, balances, policies, and CO2 factors. Calling
    this twice is a no-op for already-present rows.

    Args:
        session: An open SQLAlchemy session. The caller is responsible for
            committing (e.g. via :func:`session_scope`).
        include_missing_co2_factor_toggle: When ``True``, the ``per_km`` CO2
            factor is intentionally omitted so the carbon service later
            exercises the R12.6 "missing factor → money-only" path.

    Returns:
        A :class:`LoadSummary` with per-dataset ensured-row counts and an
        ``inserted`` map of how many rows each dataset newly inserted.
    """

    summary = LoadSummary()
    inserted: dict[str, int] = {}

    # 1. Customers (parents of orders + balances).
    n = 0
    for c in data.CUSTOMERS:
        n += _ensure(
            session, Customer, c["customerId"],
            {"customerId": c["customerId"], "name": c["name"], "city": c["city"]},
        )
    inserted["customers"] = n
    summary.customers = len(data.CUSTOMERS)

    # 4b. Green Points + Amazon Pay balances (one per customer).
    gp_n = ap_n = 0
    for c in data.CUSTOMERS:
        gp_n += _ensure(
            session, GreenPointsBalance, c["customerId"],
            {"customerId": c["customerId"], "balance": c["greenPoints"]},
        )
        ap_n += _ensure(
            session, AmazonPayBalance, c["customerId"],
            {"customerId": c["customerId"], "balanceMinor": c["amazonPayMinor"],
             "currency": data.INR},
        )
    inserted["green_points_balances"] = gp_n
    inserted["amazon_pay_balances"] = ap_n
    summary.green_points_balances = len(data.CUSTOMERS)
    summary.amazon_pay_balances = len(data.CUSTOMERS)

    # 1/10. Orders (FK → customers).
    n = 0
    for o in data.ORDERS:
        n += _ensure(
            session, Order, o["orderId"],
            {"orderId": o["orderId"], "customerId": o["customerId"],
             "deliveryDate": o["deliveryDate"], "currency": o["currency"],
             "paymentMethod": o["paymentMethod"], "sellerType": o["sellerType"]},
        )
    inserted["orders"] = n
    summary.orders = len(data.ORDERS)

    # 1/8/11. Items (FK → orders).
    n = 0
    for it in data.ITEMS:
        n += _ensure(
            session, Item, it["itemId"],
            {"itemId": it["itemId"], "orderId": it["orderId"], "category": it["category"],
             "productClassification": it["productClassification"],
             "isReturnable": it["isReturnable"], "purchasePriceMinor": it["purchasePriceMinor"],
             "currency": it["currency"], "weightGrams": it["weightGrams"],
             "title": it["title"], "photoRefs": list(it["photoRefs"])},
        )
    inserted["items"] = n
    summary.items = len(data.ITEMS)

    # 2. Charities then bins (FK → charities).
    n = 0
    for ch in data.CHARITIES:
        n += _ensure(
            session, Charity, ch["charityId"],
            {"charityId": ch["charityId"], "name": ch["name"],
             "verified": ch["verified"], "supportsWorkerPickup": ch["supportsWorkerPickup"]},
        )
    inserted["charities"] = n
    summary.charities = len(data.CHARITIES)

    n = 0
    for b in data.CHARITY_BINS:
        n += _ensure(
            session, CharityBin, b["binId"],
            {"binId": b["binId"], "charityId": b["charityId"], "city": b["city"],
             "latitude": b["latitude"], "longitude": b["longitude"], "verified": b["verified"]},
        )
    inserted["charity_bins"] = n
    summary.charity_bins = len(data.CHARITY_BINS)

    # 3. Cities.
    n = 0
    for ci in data.CITIES:
        n += _ensure(
            session, City, ci["cityId"],
            {"cityId": ci["cityId"], "name": ci["name"], "served": ci["served"],
             "centroidLat": ci["centroidLat"], "centroidLng": ci["centroidLng"]},
        )
    inserted["cities"] = n
    summary.cities = len(data.CITIES)

    # 7. Category policies.
    n = 0
    for p in data.CATEGORY_POLICIES:
        n += _ensure(
            session, CategoryPolicy, p["category"],
            {"category": p["category"], "windowDays": p["windowDays"],
             "allowableActions": list(p["allowableActions"]),
             "eligibilityCondition": p["eligibilityCondition"],
             "returnable": p["returnable"], "requiresDamageProof": p["requiresDamageProof"]},
        )
    inserted["category_policies"] = n
    summary.category_policies = len(data.CATEGORY_POLICIES)

    # 9. CO2 factors (optionally omit the per_km "missing factor" for R12.6).
    factors = [
        f for f in data.CO2_FACTORS
        if not (include_missing_co2_factor_toggle and f["factorKey"] == data.MISSING_CO2_FACTOR_KEY)
    ]
    n = 0
    for f in factors:
        n += _ensure(
            session, CO2Factor, f["factorKey"],
            {"factorKey": f["factorKey"], "value": Decimal(f["value"])},
        )
    inserted["co2_factors"] = n
    summary.co2_factors = len(factors)

    summary.inserted = inserted
    return summary


def seed_on_startup(
    *, include_missing_co2_factor_toggle: bool = False
) -> LoadSummary:
    """Create tables then load all fixtures within a committed session scope.

    Intended to be wired into FastAPI startup in a later task; calling it is
    enough to bring a fresh database to a fully seeded, demo-ready state. Safe
    to call repeatedly thanks to :func:`load_all`'s idempotency.

    In live/demo mode (``stub_mode`` is False) a set of browsable marketplace
    listings is also seeded so the Hyperlocal Marketplace has inventory out of
    the box. This is skipped under tests (which run with ``stub_mode`` True) so
    the existing fixtures and integration expectations are unchanged.
    """

    from app.config import get_settings

    init_db()
    with session_scope() as session:
        summary = load_all(
            session,
            include_missing_co2_factor_toggle=include_missing_co2_factor_toggle,
        )
        if not get_settings().stub_mode:
            load_marketplace_demo(session)
    return summary


def load_marketplace_demo(session: Session) -> int:
    """Seed browsable marketplace listings (idempotent). Returns rows inserted.

    Each listing is backed by a dedicated order, item, and RESALE return
    request so the buyer purchase flow (atomic compare-and-set + automatic
    seller refund) works without any prior setup.
    """

    inserted = 0
    for o in data.LISTING_ORDERS:
        inserted += _ensure(
            session, Order, o["orderId"],
            {"orderId": o["orderId"], "customerId": o["customerId"],
             "deliveryDate": o["deliveryDate"], "currency": o["currency"],
             "paymentMethod": o["paymentMethod"], "sellerType": o["sellerType"]},
        )
    for it in data.LISTING_ITEMS:
        inserted += _ensure(
            session, Item, it["itemId"],
            {"itemId": it["itemId"], "orderId": it["orderId"], "category": it["category"],
             "productClassification": it["productClassification"],
             "isReturnable": it["isReturnable"], "purchasePriceMinor": it["purchasePriceMinor"],
             "currency": it["currency"], "weightGrams": it["weightGrams"],
             "title": it["title"], "photoRefs": list(it["photoRefs"])},
        )
    for rr in data.RESALE_RETURN_REQUESTS:
        inserted += _ensure(
            session, ReturnRequest, rr["returnRequestId"],
            {"returnRequestId": rr["returnRequestId"], "orderId": rr["orderId"],
             "itemId": rr["itemId"], "customerId": rr["customerId"],
             "reason": rr["reason"], "returnAction": rr["returnAction"],
             "status": ReturnStatus.RESALE, "flowStep": FlowStep.PICKUP_ADDRESS,
             "itemCategory": rr["itemCategory"], "purchasePriceMinor": rr["purchasePriceMinor"],
             "currency": rr["currency"], "weightGrams": rr["weightGrams"],
             "paymentMethod": rr["paymentMethod"], "sellerType": rr["sellerType"],
             "returnWindowStart": rr["returnWindowStart"]},
        )
    for ml in data.MARKETPLACE_LISTINGS:
        inserted += _ensure(
            session, MarketplaceListing, ml["listingId"],
            {"listingId": ml["listingId"], "returnRequestId": ml["returnRequestId"],
             "city": ml["city"], "discountedPriceMinor": ml["discountedPriceMinor"],
             "currency": ml["currency"], "secondLifeScore": ml["secondLifeScore"],
             "photoRefs": list(ml["photoRefs"]), "status": ListingStatus.ACTIVE,
             "windowExpiresAt": utc_now() + timedelta(days=90),
             "pickupLocation": ml["pickupLocation"], "pickupContact": ml["pickupContact"]},
        )
    session.flush()
    return inserted


__all__ = ["LoadSummary", "load_all", "load_marketplace_demo", "seed_on_startup"]
