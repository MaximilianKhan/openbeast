"""Money.

Amounts are ``decimal.Decimal`` values in the shop currency. They cross the
wire as strings with exactly two decimals ("12.30"). All rounding to cents is
ROUND_HALF_UP and happens only where docs/API.md says it does.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
MAX_AMOUNT = Decimal("1000000.00")

_MONEY_RE = re.compile(r"^(0|[1-9][0-9]{0,6})(\.[0-9]{1,2})?$")


class MoneyFormatError(ValueError):
    pass


def parse(value) -> Decimal:
    """Parse a wire amount ("12", "12.3", "12.30") into a Decimal.

    Rejects negatives, more than two decimals, exponents, NaN/Infinity and
    anything above MAX_AMOUNT.
    """
    if isinstance(value, bool) or value is None:
        raise MoneyFormatError("not an amount")
    text = str(value).strip()
    if not _MONEY_RE.match(text):
        raise MoneyFormatError("not an amount: %r" % (text,))
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise MoneyFormatError("not an amount: %r" % (text,))
    if d > MAX_AMOUNT:
        raise MoneyFormatError("amount too large")
    return quantize(d)


def quantize(amount: Decimal) -> Decimal:
    """Round to cents, half up."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(amount: Decimal) -> str:
    """Wire form: exactly two decimals."""
    return str(quantize(amount))


def percent_of(amount: Decimal, pct: Decimal) -> Decimal:
    """``pct`` percent of ``amount``, rounded to cents."""
    return quantize(amount * pct / HUNDRED)


def clamp(amount: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, amount))
