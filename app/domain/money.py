"""Monetary and time conventions.

Money is represented **exclusively** as integer minor units (e.g. paise, cents)
alongside an ISO-4217 currency code. Floating-point types are never used for
monetary values, eliminating rounding drift in financial logic.

Time is represented as timezone-aware UTC datetimes formatted as ISO-8601.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Number of minor-unit digits per ISO-4217 currency. Most currencies use 2.
# A small table covers the common exceptions; default is 2.
_MINOR_UNIT_DIGITS: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "ISK": 0,
    "BHD": 3,
    "KWD": 3,
    "OMR": 3,
    "TND": 3,
}

# A few common symbols for friendlier formatting; falls back to the code.
_CURRENCY_SYMBOLS: dict[str, str] = {
    "INR": "\u20b9",
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "JPY": "\u00a5",
}


def _validate_currency(currency: str) -> str:
    """Validate and normalize an ISO-4217 alphabetic currency code."""

    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        raise ValueError(f"Invalid ISO-4217 currency code: {currency!r}")
    return currency.upper()


def minor_unit_digits(currency: str) -> int:
    """Return the number of minor-unit digits for an ISO-4217 currency."""

    return _MINOR_UNIT_DIGITS.get(_validate_currency(currency), 2)


@dataclass(frozen=True)
class Money:
    """An immutable monetary amount in integer minor units + ISO-4217 currency.

    Attributes:
        minor_units: The amount in the currency's smallest unit (e.g. paise).
            Always an integer; may be negative for adjustments.
        currency: The ISO-4217 alphabetic currency code (e.g. "INR").
    """

    minor_units: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.minor_units, int) or isinstance(self.minor_units, bool):
            raise TypeError("minor_units must be an int (never a float)")
        # Normalize/validate currency without mutating the frozen instance API.
        object.__setattr__(self, "currency", _validate_currency(self.currency))

    def _check_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Currency mismatch: {self.currency} vs {other.currency}"
            )

    def add(self, other: "Money") -> "Money":
        """Return the sum of two same-currency amounts."""

        self._check_same_currency(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def subtract(self, other: "Money") -> "Money":
        """Return the difference of two same-currency amounts."""

        self._check_same_currency(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def compare(self, other: "Money") -> int:
        """Return -1, 0, or 1 comparing this amount to another (same currency)."""

        self._check_same_currency(other)
        if self.minor_units < other.minor_units:
            return -1
        if self.minor_units > other.minor_units:
            return 1
        return 0

    def format(self) -> str:
        """Render a human-readable amount, e.g. ``\u20b91,299.00``."""

        return format_money(self.minor_units, self.currency)


def add(a_minor: int, b_minor: int) -> int:
    """Add two amounts expressed in the same currency's minor units."""

    if isinstance(a_minor, bool) or isinstance(b_minor, bool):
        raise TypeError("money amounts must be int, not bool")
    if not isinstance(a_minor, int) or not isinstance(b_minor, int):
        raise TypeError("money amounts must be integers in minor units")
    return a_minor + b_minor


def compare(a_minor: int, b_minor: int) -> int:
    """Compare two minor-unit amounts; return -1, 0, or 1."""

    if a_minor < b_minor:
        return -1
    if a_minor > b_minor:
        return 1
    return 0


def format_money(minor_units: int, currency: str) -> str:
    """Format an integer minor-unit amount as a localized currency string.

    Example: ``format_money(129900, "INR") -> "\u20b91,299.00"``.
    """

    if isinstance(minor_units, bool) or not isinstance(minor_units, int):
        raise TypeError("minor_units must be an int (never a float)")
    code = _validate_currency(currency)
    digits = minor_unit_digits(code)
    symbol = _CURRENCY_SYMBOLS.get(code, code + " ")

    negative = minor_units < 0
    value = abs(minor_units)

    if digits == 0:
        whole = value
        body = f"{whole:,}"
    else:
        scale = 10 ** digits
        whole, frac = divmod(value, scale)
        body = f"{whole:,}.{frac:0{digits}d}"

    sign = "-" if negative else ""
    return f"{sign}{symbol}{body}"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""

    return datetime.now(timezone.utc)


def to_iso8601(moment: datetime) -> str:
    """Format a datetime as an ISO-8601 UTC string ending in ``Z``.

    Naive datetimes are assumed to be UTC; aware datetimes are converted to UTC.
    """

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    # Use Z suffix instead of +00:00 for canonical UTC ISO-8601.
    return moment.isoformat().replace("+00:00", "Z")
