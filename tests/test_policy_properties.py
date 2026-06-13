"""Property-based tests for the category-policy / returnability gate (task 4).

Covers design Correctness Properties 35-38 for ``app.domain.policy``. Each test
is tagged with the exact ``Feature: secondlife-ai, Property {n}: {text}`` comment
and runs against the Hypothesis ``ci`` profile (>= 100 examples; see
``tests/conftest.py``).

The policy module is pure and deterministic, so these tests need no database or
``STUB_MODE`` AI paths — they exercise the logic directly across many inputs.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from app.domain import policy
from app.domain.models import ItemCategory, ReturnAction
from app.fixtures.seed_data import (
    CATEGORY_POLICIES,
    NON_RETURNABLE_CLASSIFICATIONS,
)

# --- Reusable strategies ---------------------------------------------------

ALL_CATEGORIES = list(ItemCategory)
ALL_ACTIONS = list(ReturnAction)

# Policy rows indexed by the (string) category value, for table-equality checks.
_POLICY_ROW_BY_VALUE = {row["category"]: row for row in CATEGORY_POLICIES}

# Policy categories that have a finite return window (windowDays is not None),
# paired with their window length. Demo categories resolve to these.
_CATEGORIES_WITH_WINDOW: list[tuple[ItemCategory, int]] = []
_CATEGORIES_NO_WINDOW: list[ItemCategory] = []
for _cat in ALL_CATEGORIES:
    _view = policy.get_policy(_cat)
    if _view.window_days is None:
        _CATEGORIES_NO_WINDOW.append(_cat)
    else:
        _CATEGORIES_WITH_WINDOW.append((_cat, _view.window_days))

categories = st.sampled_from(ALL_CATEGORIES)
actions = st.sampled_from(ALL_ACTIONS)
delivery_dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31))

# A classification that is NOT blacklisted (so returnability hinges on category).
_SAFE_CLASSIFICATION = "GENERIC_WIDGET"
assert _SAFE_CLASSIFICATION not in NON_RETURNABLE_CLASSIFICATIONS


# ---------------------------------------------------------------------------
# Property 35 — return action restricted to the category allowable set
# ---------------------------------------------------------------------------
# Feature: secondlife-ai, Property 35: Return action restricted to the category allowable set
# Validates: Requirements 13.1, 13.2, 13.3, 13.4
@given(category=categories, action=actions, delivery=delivery_dates, offset=st.integers(0, 5))
def test_property_35_action_restricted_to_allowable_set(
    category: ItemCategory, action: ReturnAction, delivery: date, offset: int
) -> None:
    allowed = policy.allowable_actions(category)
    resolved = policy.resolve_policy_category(category)
    expected = frozenset(
        ReturnAction(a) for a in _POLICY_ROW_BY_VALUE[resolved.value]["allowableActions"]
    )

    # The allowable set equals the policy-table row for the resolved category.
    assert allowed == expected
    # Every allowable action is one of the three platform return actions (R13.2).
    assert allowed <= {ReturnAction.REFUND, ReturnAction.REPLACEMENT, ReturnAction.EXCHANGE}

    # is_action_allowed agrees with set membership, and out-of-set is rejected.
    assert policy.is_action_allowed(category, action) is (action in allowed)

    # Drive the full gate with a returnable, in-window submission so the only
    # remaining gate is the allowable-action restriction (R13.1, R13.4).
    submission = delivery + timedelta(days=offset)
    result = policy.evaluate_initiation_eligibility(
        product_classification=_SAFE_CLASSIFICATION,
        category=category,
        delivery_date=delivery,
        submission=submission,
        action=action,
    )
    if policy.is_returnable(_SAFE_CLASSIFICATION, category) and policy.within_return_window(
        category, delivery, submission
    ):
        if action in allowed:
            assert result.eligible is True
            assert result.rejection_code is None
        else:
            assert result.eligible is False
            assert result.rejection_code == policy.REJECT_ACTION_NOT_ALLOWED


# ---------------------------------------------------------------------------
# Property 36 — category window boundary correctness
# ---------------------------------------------------------------------------
# Feature: secondlife-ai, Property 36: Category window boundary correctness
# Validates: Requirements 14.1, 14.9
@given(
    cat_window=st.sampled_from(_CATEGORIES_WITH_WINDOW),
    delivery=delivery_dates,
    offset_days=st.integers(min_value=-5, max_value=120),
)
def test_property_36_window_boundary(
    cat_window: tuple[ItemCategory, int], delivery: date, offset_days: int
) -> None:
    category, window_days = cat_window
    submission = delivery + timedelta(days=offset_days)

    # Delivery counts as day 1, so the final in-window calendar day is
    # delivery + (window_days - 1). In-window iff submission <= that final day.
    final_day = delivery + timedelta(days=window_days - 1)
    expected = submission <= final_day

    assert policy.within_return_window(category, delivery, submission) is expected

    # Boundary witnesses: exactly the final day is in, the next day is out (R14.9).
    assert policy.within_return_window(category, delivery, final_day) is True
    assert (
        policy.within_return_window(category, delivery, final_day + timedelta(days=1))
        is False
    )


# Feature: secondlife-ai, Property 36: Category window boundary correctness
# Validates: Requirements 14.1, 14.9
@given(
    category=st.sampled_from(_CATEGORIES_NO_WINDOW),
    delivery=delivery_dates,
    offset_days=st.integers(min_value=-5, max_value=120),
)
def test_property_36_no_window_categories_never_in_window(
    category: ItemCategory, delivery: date, offset_days: int
) -> None:
    # Categories with no return window (non-returnable categories) are never
    # in-window regardless of dates (window not applicable).
    submission = delivery + timedelta(days=offset_days)
    assert policy.within_return_window(category, delivery, submission) is False


# ---------------------------------------------------------------------------
# Property 37 — category policy table enforcement
# ---------------------------------------------------------------------------
# Feature: secondlife-ai, Property 37: Category policy table enforcement
# Validates: Requirements 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.10, 14.11
@given(category=categories, probe_action=actions, delivery=delivery_dates, offset=st.integers(-3, 60))
def test_property_37_policy_table_enforcement(
    category: ItemCategory, probe_action: ReturnAction, delivery: date, offset: int
) -> None:
    view = policy.get_policy(category)
    resolved = policy.resolve_policy_category(category)
    row = _POLICY_ROW_BY_VALUE[resolved.value]

    # Every field of the returned view equals the resolved CategoryPolicy row.
    assert view.category == resolved
    assert view.window_days == row["windowDays"]
    assert view.allowable_actions == frozenset(
        ReturnAction(a) for a in row["allowableActions"]
    )
    assert view.returnable is bool(row["returnable"])
    assert view.eligibility_condition == row["eligibilityCondition"]
    assert view.requires_damage_proof is bool(row["requiresDamageProof"])

    # Accessors agree with the view (and thus the table) for any probe input.
    assert policy.allowable_actions(category) == view.allowable_actions
    assert policy.eligibility_condition(category) == view.eligibility_condition
    assert policy.requires_damage_proof(category) is view.requires_damage_proof
    assert policy.is_action_allowed(category, probe_action) is (
        probe_action in view.allowable_actions
    )
    # Window accessor is consistent with the table's windowDays for any date.
    submission = delivery + timedelta(days=offset)
    in_window = policy.within_return_window(category, delivery, submission)
    if view.window_days is None:
        assert in_window is False


# ---------------------------------------------------------------------------
# Property 38 — non-returnable rejection and returnability-before-window order
# ---------------------------------------------------------------------------
# Categories whose policy marks them non-returnable.
_NON_RETURNABLE_CATEGORIES = [
    c for c in ALL_CATEGORIES if not policy.get_policy(c).returnable
]

# Strategy producing (productClassification, category) pairs that are
# non-returnable either via the blacklist or via a non-returnable category.
non_returnable_inputs = st.one_of(
    # Blacklisted classification with any category.
    st.tuples(st.sampled_from(sorted(NON_RETURNABLE_CLASSIFICATIONS)), categories),
    # Safe classification but a non-returnable category.
    st.tuples(
        st.just(_SAFE_CLASSIFICATION), st.sampled_from(_NON_RETURNABLE_CATEGORIES)
    ),
)


# Feature: secondlife-ai, Property 38: Non-returnable rejection and returnability-before-window ordering
# Validates: Requirements 15.1, 15.2, 15.3, 15.4
@given(
    classification_and_category=non_returnable_inputs,
    delivery=delivery_dates,
    days_after_window=st.integers(min_value=1, max_value=400),
    action=actions,
)
def test_property_38_non_returnable_and_ordering(
    classification_and_category: tuple[str, ItemCategory],
    delivery: date,
    days_after_window: int,
    action: ReturnAction,
) -> None:
    classification, category = classification_and_category

    # 1) Non-returnable items are never returnable (R15.1).
    assert policy.is_returnable(classification, category) is False

    # 2) Construct a submission that is ALSO past any window, so if the gate
    #    consulted the window first it would (wrongly) report WINDOW_ELAPSED.
    #    A non-returnable category has no window, but a blacklisted classification
    #    may sit on a category that does; pick a date far past the longest window.
    submission = delivery + timedelta(days=days_after_window + 400)

    result = policy.evaluate_initiation_eligibility(
        product_classification=classification,
        category=category,
        delivery_date=delivery,
        submission=submission,
        action=action,
    )

    # Rejected as non-returnable, no request created (eligible is False)...
    assert result.eligible is False
    assert result.rejection_code == policy.REJECT_NON_RETURNABLE
    # ...and the window was NEVER evaluated (returnability is checked first),
    # which is why the code is NON_RETURNABLE even though the date is out of
    # window (R15.3, R15.4).
    assert result.window_evaluated is False


# Feature: secondlife-ai, Property 38: Non-returnable rejection and returnability-before-window ordering
# Validates: Requirements 15.1, 15.2, 15.3, 15.4
@given(
    category=categories,
    safe_token=st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ_", min_size=1, max_size=12).filter(
        lambda t: t.upper() not in NON_RETURNABLE_CLASSIFICATIONS
    ),
)
def test_property_38_returnable_matches_category_and_blacklist(
    category: ItemCategory, safe_token: str
) -> None:
    # A non-blacklisted classification is returnable iff the category is returnable.
    assert policy.is_returnable(safe_token, category) is (
        policy.get_policy(category).returnable
    )
    # Any blacklisted classification forces non-returnable regardless of category.
    for token in NON_RETURNABLE_CLASSIFICATIONS:
        assert policy.is_returnable(token, category) is False
