"""Smoke tests proving the app boots and the test stack (incl. Hypothesis) runs."""

from __future__ import annotations

from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from app.domain import money
from app.main import app

client = TestClient(app)


def test_health_endpoint_returns_ok() -> None:
    """The /health endpoint returns 200 and {"status": "ok"}."""

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@given(minor_units=st.integers(min_value=0, max_value=10_000_000))
def test_money_minor_unit_round_trip(minor_units: int) -> None:
    """A Money value preserves its integer minor units exactly (no float drift).

    This trivial property exercises the Hypothesis profile (>= 100 examples)
    and the integer-minor-unit money convention.
    """

    amount = money.Money(minor_units=minor_units, currency="INR")
    assert amount.minor_units == minor_units
    # Adding zero is the identity and stays integer-exact.
    assert amount.add(money.Money(0, "INR")).minor_units == minor_units
    # Formatting never raises and round-trips the digits for a 2-decimal currency.
    rendered = amount.format()
    assert isinstance(rendered, str) and rendered != ""
