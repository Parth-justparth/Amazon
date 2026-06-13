"""Property-based tests for the Carbon_Savings_Service (task 11).

Covers design Correctness Properties 33 and 34 for
``app.services.carbon_savings``. Each test is tagged with the exact
``Feature: secondlife-ai, Property {n}: {text}`` comment and a
``Validates: Requirements ...`` line, and runs against the Hypothesis ``ci``
profile (>= 100 examples; see ``tests/conftest.py``).

A fresh in-memory SQLite database is built per example with the ``CO2_Factor``
rows loaded directly from the seed dataset, so the formula under test is
compared against the same configured factor values computed independently.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.models import Base, CO2Factor, Disposition
from app.domain.money import format_money
from app.fixtures.seed_data import CO2_FACTORS
from app.services.carbon_savings import (
    build_impact_message,
    compute_carbon_savings,
    format_co2,
)

# ---------------------------------------------------------------------------
# Fresh in-memory database (CO2 factors only) per example
# ---------------------------------------------------------------------------


def _make_co2_factory():
    """Create a disposable in-memory engine with the CO2_Factor rows loaded."""

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
        for f in CO2_FACTORS:
            session.add(CO2Factor(factorKey=f["factorKey"], value=Decimal(f["value"])))
        session.commit()
    finally:
        session.close()
    return engine, factory


def _factor_map(session) -> dict[str, Decimal]:
    """Read the seeded CO2_Factor rows into a ``factorKey -> Decimal`` map."""

    return {row.factorKey: Decimal(row.value) for row in session.scalars(select(CO2Factor))}


def _expected_formula(
    factors: dict[str, Decimal],
    disposition: Disposition,
    weight_grams: int,
    avoided_distance_km: int,
) -> Decimal:
    """Independently compute the configured-factor formula (warehouse -> 0)."""

    if disposition == Disposition.WAREHOUSE_RETURN:
        return Decimal("0")
    return (
        factors[f"disposition:{disposition.value}"]
        + factors["per_km"] * Decimal(avoided_distance_km)
        + factors["per_kg"] * (Decimal(weight_grams) / Decimal(1000))
    )


_ALL_DISPOSITIONS = list(Disposition)
_NON_WAREHOUSE = [d for d in Disposition if d != Disposition.WAREHOUSE_RETURN]


# ---------------------------------------------------------------------------
# Property 33 — Carbon savings non-negative, formula-correct, zero for warehouse
# ---------------------------------------------------------------------------
# Feature: secondlife-ai, Property 33: Carbon savings non-negative, formula-correct, zero for warehouse
# Validates: Requirements 12.1, 12.2, 12.5
@given(
    disposition=st.sampled_from(_ALL_DISPOSITIONS),
    weight_grams=st.integers(min_value=0, max_value=100_000),
    avoided_distance_km=st.integers(min_value=0, max_value=1_000),
)
def test_property_33_carbon_savings_formula(
    disposition: Disposition, weight_grams: int, avoided_distance_km: int
) -> None:
    engine, factory = _make_co2_factory()
    try:
        session = factory()
        try:
            factors = _factor_map(session)
            result = compute_carbon_savings(
                session, disposition, weight_grams, avoided_distance_km
            )

            expected = _expected_formula(
                factors, disposition, weight_grams, avoided_distance_km
            )

            # Equals the configured factor formula (R12.2) ...
            assert result == expected
            # ... is non-negative (R12.1) ...
            assert result >= 0
            # ... and is exactly 0 for a warehouse return (R12.5).
            if disposition == Disposition.WAREHOUSE_RETURN:
                assert result == Decimal("0")
        finally:
            session.close()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 34 — Impact message contains money saved and CO2 saved
# ---------------------------------------------------------------------------
# Feature: secondlife-ai, Property 34: Impact message contains money saved and CO2 saved
# Validates: Requirements 12.3
@given(
    disposition=st.sampled_from(_NON_WAREHOUSE),
    weight_grams=st.integers(min_value=0, max_value=100_000),
    avoided_distance_km=st.integers(min_value=0, max_value=1_000),
    money_saved_minor=st.integers(min_value=0, max_value=10_000_000),
    currency=st.sampled_from(["INR", "USD", "EUR"]),
)
def test_property_34_impact_message_content(
    disposition: Disposition,
    weight_grams: int,
    avoided_distance_km: int,
    money_saved_minor: int,
    currency: str,
) -> None:
    engine, factory = _make_co2_factory()
    try:
        session = factory()
        try:
            carbon = compute_carbon_savings(
                session, disposition, weight_grams, avoided_distance_km
            )
            message = build_impact_message(
                money_saved_minor, currency, carbon, disposition
            )

            # The rendered message includes the money saved in the order currency ...
            assert format_money(money_saved_minor, currency) in message
            # ... and the Carbon_Savings expressed in kilograms of CO2 (R12.3).
            assert format_co2(carbon) in message
            assert "kg of CO2" in message
        finally:
            session.close()
    finally:
        engine.dispose()
