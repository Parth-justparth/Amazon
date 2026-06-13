"""Category policy and returnability gate (pure, deterministic).

This module encodes the Amazon SecondLife AI category-policy table (design
"Datasets" section 7) and the non-returnable blacklist (section 8), exposing the
returnability / return-window / allowable-action logic used by the
``Return_Initiation_Service`` initiation gate (R13, R14, R15).

Design intent
-------------
The functions here are **pure and deterministic**: they perform no database
writes and depend only on the declarative seed data
(:data:`app.fixtures.seed_data.CATEGORY_POLICIES`,
:data:`app.fixtures.seed_data.NON_RETURNABLE_CLASSIFICATIONS`) plus a small
demo→policy category resolver. This keeps the gate trivially testable and lets
the (later) HTTP service in ``app.services.return_initiation`` call into the
single high-level :func:`evaluate_initiation_eligibility` without re-deriving
policy.

Ordered initiation gate (strictly enforced — design "Initiation gate ordering",
R15.3, R15.4):

1. **Returnability first** — if :func:`is_returnable` is false (blacklisted
   ``productClassification`` *or* a non-returnable policy category), reject as
   ``NON_RETURNABLE`` **without ever evaluating the return window** (R15.1,
   R15.2, R15.4).
2. **Category return window** — the ``Category_Return_Window`` is measured from
   the delivery date counted as **day 1**; a request on or before 23:59:59 of
   the final calendar day is in-window (R14.1). Otherwise ``WINDOW_ELAPSED``
   (R14.9).
3. **Allowable action** — the selected ``Return_Action`` must lie in the
   category's ``Allowable_Return_Action_Set`` (R13.1, R13.4); otherwise
   ``ACTION_NOT_ALLOWED``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from datetime import timedelta

from app.domain.models import ItemCategory, ReturnAction
from app.fixtures.seed_data import (
    CATEGORY_POLICIES,
    DEMO_TO_POLICY_CATEGORY,
    NON_RETURNABLE_CLASSIFICATIONS,
    POLICY_CATEGORY_DISPLAY_NAMES,
)

__all__ = [
    "REJECT_NON_RETURNABLE",
    "REJECT_WINDOW_ELAPSED",
    "REJECT_ACTION_NOT_ALLOWED",
    "CategoryPolicyView",
    "EligibilityResult",
    "resolve_policy_category",
    "get_policy",
    "is_returnable",
    "within_return_window",
    "allowable_actions",
    "is_action_allowed",
    "eligibility_condition",
    "requires_damage_proof",
    "evaluate_initiation_eligibility",
]


# ---------------------------------------------------------------------------
# Rejection codes (mirror the design "Validation and Client Errors" mapping)
# ---------------------------------------------------------------------------
REJECT_NON_RETURNABLE = "NON_RETURNABLE"
REJECT_WINDOW_ELAPSED = "WINDOW_ELAPSED"
REJECT_ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Build an immutable, normalized policy index keyed by the policy ItemCategory.
# ---------------------------------------------------------------------------
def _index_policies() -> dict[ItemCategory, "CategoryPolicyView"]:
    index: dict[ItemCategory, CategoryPolicyView] = {}
    for row in CATEGORY_POLICIES:
        category = ItemCategory(row["category"])
        actions = frozenset(ReturnAction(a) for a in row["allowableActions"])
        index[category] = CategoryPolicyView(
            category=category,
            window_days=row["windowDays"],
            allowable_actions=actions,
            eligibility_condition=row["eligibilityCondition"],
            returnable=bool(row["returnable"]),
            requires_damage_proof=bool(row["requiresDamageProof"]),
            display_name=POLICY_CATEGORY_DISPLAY_NAMES.get(category, category.value),
        )
    return index


@dataclass(frozen=True)
class CategoryPolicyView:
    """Immutable view of a single ``CategoryPolicy`` row (design dataset 7).

    A plain dataclass (rather than the SQLAlchemy ORM object) so the policy gate
    stays pure and is trivial to construct in tests.
    """

    category: ItemCategory
    window_days: int | None
    allowable_actions: frozenset[ReturnAction]
    eligibility_condition: str
    returnable: bool
    requires_damage_proof: bool
    display_name: str = ""


@dataclass(frozen=True)
class EligibilityResult:
    """Structured outcome of the ordered initiation gate (pure; no DB rows).

    Attributes:
        eligible: True only when returnability, window, and allowable action all
            pass.
        rejection_code: ``None`` when eligible; otherwise one of
            :data:`REJECT_NON_RETURNABLE`, :data:`REJECT_WINDOW_ELAPSED`,
            :data:`REJECT_ACTION_NOT_ALLOWED`.
        reason: A human-readable explanation suitable for the API message.
        window_evaluated: True only if the gate actually consulted the return
            window. This is **false** for a non-returnable rejection, proving
            returnability is evaluated first (R15.3, R15.4).
        allowable_actions: The category's allowable action set (for messaging).
    """

    eligible: bool
    rejection_code: str | None = None
    reason: str | None = None
    window_evaluated: bool = False
    allowable_actions: frozenset[ReturnAction] = field(default_factory=frozenset)


_POLICY_INDEX: dict[ItemCategory, CategoryPolicyView] = _index_policies()


# ---------------------------------------------------------------------------
# Resolution + lookup
# ---------------------------------------------------------------------------
def resolve_policy_category(category: ItemCategory) -> ItemCategory:
    """Map a demo ``ItemCategory`` to its governing policy category.

    Demo categories (ELECTRONICS / HOME_APPLIANCES / FOOTWEAR) resolve through
    :data:`DEMO_TO_POLICY_CATEGORY`; policy categories pass through unchanged.
    """

    return DEMO_TO_POLICY_CATEGORY.get(category, category)


def get_policy(category: ItemCategory) -> CategoryPolicyView:
    """Return the :class:`CategoryPolicyView` governing ``category``.

    Resolves a demo category to its policy category first (R14, R15).

    Raises:
        KeyError: if the resolved category has no policy row (should not happen
            for any defined :class:`ItemCategory`).
    """

    resolved = resolve_policy_category(category)
    return _POLICY_INDEX[resolved]


# ---------------------------------------------------------------------------
# Returnability (R15) — MUST be evaluated before the window (R15.3, R15.4)
# ---------------------------------------------------------------------------
def is_returnable(product_classification: str | None, category: ItemCategory) -> bool:
    """Return whether an item may be returned at all (``Item_Returnability``).

    False when the ``productClassification`` is in the non-returnable blacklist
    (R15.1) OR the resolved category policy's ``returnable`` flag is false
    (R14.6-14.8); True otherwise. This check is independent of, and evaluated
    before, the ``Category_Return_Window`` (R15.3, R15.4).
    """

    if product_classification is not None:
        token = product_classification.strip().upper()
        if token in NON_RETURNABLE_CLASSIFICATIONS:
            return False
    return get_policy(category).returnable


# ---------------------------------------------------------------------------
# Return window (R14.1, R14.9)
# ---------------------------------------------------------------------------
def _window_cutoff(delivery_date: date, window_days: int, tzinfo) -> datetime:
    """Compute the inclusive end-of-window instant: 23:59:59 of the final day.

    The delivery date counts as day 1, so the final calendar day is
    ``delivery_date + (window_days - 1)`` (R14.1).
    """

    final_day = delivery_date + timedelta(days=window_days - 1)
    return datetime.combine(final_day, time(23, 59, 59), tzinfo=tzinfo)


def within_return_window(
    category: ItemCategory,
    delivery_date: date,
    submission: date | datetime,
) -> bool:
    """Return whether ``submission`` falls within the ``Category_Return_Window``.

    The window is measured from ``delivery_date`` counted as **day 1**; a
    request on or before 23:59:59 of the final calendar day is in-window
    (R14.1). Categories with no window (``window_days is None``; non-returnable
    categories) are never in-window (not applicable).

    ``submission`` may be a :class:`datetime.date` (compared by calendar day) or
    an aware/naive :class:`datetime.datetime` (compared against 23:59:59 of the
    final day in the submission's own timezone), keeping the result
    deterministic.
    """

    view = get_policy(category)
    if view.window_days is None:
        return False

    # datetime is a subclass of date, so check the more specific type first.
    if isinstance(submission, datetime):
        cutoff = _window_cutoff(delivery_date, view.window_days, submission.tzinfo)
        return submission <= cutoff

    final_day = delivery_date + timedelta(days=view.window_days - 1)
    return submission <= final_day


# ---------------------------------------------------------------------------
# Allowable actions (R13, R14)
# ---------------------------------------------------------------------------
def allowable_actions(category: ItemCategory) -> frozenset[ReturnAction]:
    """Return the ``Allowable_Return_Action_Set`` for ``category`` (R13.4, R14)."""

    return get_policy(category).allowable_actions


def is_action_allowed(category: ItemCategory, action: ReturnAction) -> bool:
    """Return whether ``action`` is permitted for ``category`` (R13.1, R13.4)."""

    return action in get_policy(category).allowable_actions


# ---------------------------------------------------------------------------
# Eligibility condition + damage proof (R14.2-14.8, R14.11)
# ---------------------------------------------------------------------------
def eligibility_condition(category: ItemCategory) -> str:
    """Return the machine-checkable eligibility-condition token (R14.2-14.8)."""

    return get_policy(category).eligibility_condition


def requires_damage_proof(category: ItemCategory) -> bool:
    """Return whether a damage claim requires proof (appliances; R14.5, R14.11)."""

    return get_policy(category).requires_damage_proof


# ---------------------------------------------------------------------------
# High-level ordered gate (pure; the HTTP service in task 5 wraps this)
# ---------------------------------------------------------------------------
def evaluate_initiation_eligibility(
    product_classification: str | None,
    category: ItemCategory,
    delivery_date: date,
    submission: date | datetime,
    action: ReturnAction,
) -> EligibilityResult:
    """Apply the ordered initiation gate without creating any DB rows.

    Order (design "Initiation gate ordering"):

    1. **Returnability first** (R15.3, R15.4) — reject ``NON_RETURNABLE`` before
       touching the window.
    2. **Return window** (R14.1, R14.9) — reject ``WINDOW_ELAPSED`` if elapsed.
    3. **Allowable action** (R13.1, R13.4) — reject ``ACTION_NOT_ALLOWED`` if the
       action is outside the category set.

    Returns an :class:`EligibilityResult`; ``window_evaluated`` is true only when
    the window was actually consulted (i.e. never for a non-returnable item),
    which witnesses the returnability-before-window ordering.
    """

    actions = allowable_actions(category)

    # 1) Returnability — evaluated FIRST, short-circuits before the window.
    if not is_returnable(product_classification, category):
        return EligibilityResult(
            eligible=False,
            rejection_code=REJECT_NON_RETURNABLE,
            reason="Item is non-returnable per policy.",
            window_evaluated=False,
            allowable_actions=actions,
        )

    # 2) Category return window.
    if not within_return_window(category, delivery_date, submission):
        return EligibilityResult(
            eligible=False,
            rejection_code=REJECT_WINDOW_ELAPSED,
            reason="The return window has elapsed.",
            window_evaluated=True,
            allowable_actions=actions,
        )

    # 3) Allowable action restriction.
    if action not in actions:
        allowed = ", ".join(sorted(a.value for a in actions)) or "(none)"
        return EligibilityResult(
            eligible=False,
            rejection_code=REJECT_ACTION_NOT_ALLOWED,
            reason=f"Allowable return actions for this category: {allowed}.",
            window_evaluated=True,
            allowable_actions=actions,
        )

    return EligibilityResult(
        eligible=True,
        rejection_code=None,
        reason=None,
        window_evaluated=True,
        allowable_actions=actions,
    )
