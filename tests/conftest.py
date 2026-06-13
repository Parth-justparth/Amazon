"""Shared pytest fixtures and Hypothesis configuration.

Registers a ``ci`` Hypothesis profile with ``max_examples=100`` (the spec's
minimum for property-based tests) and a reduced ``fast`` profile
(``max_examples=20``). The ``ci`` profile is loaded by default so the full 100
examples run unless ``HYPOTHESIS_PROFILE=fast`` is set for a quick local run.
Also exposes a fresh in-memory SQLite session fixture for use by
later tests, and ensures ``STUB_MODE`` is enabled for the whole test run.
"""

from __future__ import annotations

import os

# Ensure deterministic, fixture-backed AI paths for the entire test session.
# We explicitly override rather than setdefault to beat any local .env configurations
os.environ["SECONDLIFE_STUB_MODE"] = "true"

import pytest
from hypothesis import HealthCheck, settings
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# --- Hypothesis profiles -------------------------------------------------

settings.register_profile(
    "ci",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "dev",
    max_examples=25,
    deadline=None,
)
# Reduced-example profile for fast local runs. Same deadline/health-check
# behavior as "ci" but far fewer examples so the (slow) property tests finish
# quickly during development.
settings.register_profile(
    "fast",
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Load the profile named by HYPOTHESIS_PROFILE, defaulting to the 100-example
# "ci" profile so property tests always run >= 100 examples by default, as the
# spec mandates. Set HYPOTHESIS_PROFILE=fast for a quick (20-example) local run.
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


# --- Database fixtures ---------------------------------------------------


@pytest.fixture
def db_session() -> Session:
    """Yield a fresh, isolated in-memory SQLite session per test.

    Uses a StaticPool so the in-memory database persists across connections
    within a single test. Later tasks attach the ORM ``Base.metadata`` here to
    create tables; for now it provides a usable, disposable session.
    """

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
