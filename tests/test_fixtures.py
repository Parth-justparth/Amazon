"""Tests for the seed datasets and the idempotent fixture loader (task 3.1).

These tests run a real ``init_db()`` + ``load_all()`` against an isolated
in-memory SQLite database and assert:

* every dataset loads with the expected row counts,
* the exact demo monetary values (minor units) match the design,
* foreign-key consistency holds (every Item.orderId references a real Order),
* the loader is idempotent (a second load adds nothing and does not raise),
* the "missing factor" toggle omits ``per_km``.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import (
    AmazonPayBalance,
    Base,
    CategoryPolicy,
    Charity,
    CharityBin,
    City,
    CO2Factor,
    Customer,
    GreenPointsBalance,
    Item,
    ItemCategory,
    Order,
)
from app.fixtures import seed_data as data
from app.fixtures.loader import load_all


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


def _count(session: Session, model: type) -> int:
    return session.scalar(sa.select(sa.func.count()).select_from(model))


def test_catalog_loads_with_expected_counts(session: Session) -> None:
    """All datasets load with the row counts declared in seed_data."""

    summary = load_all(session)
    session.commit()

    assert _count(session, Customer) == len(data.CUSTOMERS) == 3
    assert _count(session, Order) == len(data.ORDERS) == 8
    assert _count(session, Item) == len(data.ITEMS) == 8
    assert _count(session, Charity) == 2
    assert _count(session, CharityBin) == 3
    assert _count(session, City) == 3
    assert _count(session, GreenPointsBalance) == 3
    assert _count(session, AmazonPayBalance) == 3
    assert summary.as_dict()["items"] == 8


def test_category_policies_count_and_content(session: Session) -> None:
    """Exactly the seven policy rows load, with key fields intact."""

    load_all(session)
    session.commit()

    assert _count(session, CategoryPolicy) == 7

    appliances = session.get(
        CategoryPolicy, ItemCategory.HOME_KITCHEN_APPLIANCES.value
    )
    assert appliances.windowDays == 10
    assert appliances.requiresDamageProof is True
    assert appliances.allowableActions == ["REPLACEMENT"]

    software = session.get(
        CategoryPolicy, ItemCategory.SOFTWARE_VIDEO_GAMES_MUSIC.value
    )
    assert software.windowDays is None
    assert software.returnable is False
    assert software.allowableActions == []


def test_co2_factors_present(session: Session) -> None:
    """All six CO2 factors load and warehouse records 0.0 (R12.5)."""

    load_all(session)
    session.commit()

    assert _count(session, CO2Factor) == 6
    warehouse = session.get(CO2Factor, "disposition:WAREHOUSE_RETURN")
    assert float(warehouse.value) == 0.0
    assert session.get(CO2Factor, "per_km") is not None


def test_missing_co2_factor_toggle_omits_per_km(session: Session) -> None:
    """The toggle drops per_km to exercise the R12.6 money-only path."""

    load_all(session, include_missing_co2_factor_toggle=True)
    session.commit()

    assert _count(session, CO2Factor) == 5
    assert session.get(CO2Factor, "per_km") is None


def test_keep_it_item_price_is_exact_minor_units(session: Session) -> None:
    """item_keepit_01 follows the design worked example: P == 38_990 minor."""

    load_all(session)
    session.commit()

    keepit = session.get(Item, "item_keepit_01")
    assert keepit.purchasePriceMinor == 38_990  # ₹389.90, NOT 3_899_000
    assert keepit.weightGrams == 11_500
    assert keepit.category is ItemCategory.HOME_APPLIANCES


def test_headphones_price_is_exact_minor_units(session: Session) -> None:
    """item_elec_01 headphones price equals ₹12,999 → 1_299_900 minor."""

    load_all(session)
    session.commit()

    headphones = session.get(Item, "item_elec_01")
    assert headphones.purchasePriceMinor == 1_299_900
    # The AC unit is ₹38,990 (3_899_000), distinct from the Keep It blender.
    ac_unit = session.get(Item, "item_appl_01")
    assert ac_unit.purchasePriceMinor == 3_899_000


def test_non_returnable_item_flag(session: Session) -> None:
    """item_nr_01 is INNERWEAR and not returnable (R15.2)."""

    load_all(session)
    session.commit()

    item = session.get(Item, "item_nr_01")
    assert item.isReturnable is False
    assert item.productClassification == "INNERWEAR"
    assert item.productClassification in data.NON_RETURNABLE_CLASSIFICATIONS


def test_served_and_unserved_cities(session: Session) -> None:
    """Bengaluru/Delhi are served; the Tier-3 town is not (R5.8)."""

    load_all(session)
    session.commit()

    assert session.get(City, "city_blr").served is True
    assert session.get(City, "city_del").served is True
    xyz = session.get(City, "city_xyz")
    assert xyz.served is False
    assert xyz.centroidLat is None


def test_customer_balances(session: Session) -> None:
    """Customer balances match the design (amounts in minor units)."""

    load_all(session)
    session.commit()

    assert session.get(GreenPointsBalance, "cust_02").balance == 1500
    assert session.get(AmazonPayBalance, "cust_02").balanceMinor == 25_000  # ₹250
    assert session.get(AmazonPayBalance, "buyer_22").balanceMinor == 500_000  # ₹5,000
    assert session.get(GreenPointsBalance, "cust_01").balance == 0


def test_foreign_key_consistency(session: Session) -> None:
    """Every Item.orderId references a real Order and bins reference charities."""

    load_all(session)
    session.commit()

    order_ids = {o.orderId for o in session.scalars(sa.select(Order)).all()}
    for item in session.scalars(sa.select(Item)).all():
        assert item.orderId in order_ids

    charity_ids = {c.charityId for c in session.scalars(sa.select(Charity)).all()}
    for b in session.scalars(sa.select(CharityBin)).all():
        assert b.charityId in charity_ids

    customer_ids = {c.customerId for c in session.scalars(sa.select(Customer)).all()}
    for o in session.scalars(sa.select(Order)).all():
        assert o.customerId in customer_ids


def test_loader_is_idempotent(session: Session) -> None:
    """Calling load_all twice does not raise or duplicate rows."""

    load_all(session)
    session.commit()

    second = load_all(session)
    session.commit()

    # Nothing new inserted on the second pass.
    assert all(v == 0 for v in second.inserted.values())
    assert _count(session, Item) == len(data.ITEMS)
    assert _count(session, Order) == len(data.ORDERS)
    assert _count(session, CategoryPolicy) == 7
    assert _count(session, CO2Factor) == 6
