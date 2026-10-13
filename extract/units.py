"""Printed unit strings to a unit class and a scale, by a hand-written table (Step 10).

A metric value's key holds its unit class (CONTEXT.md), so a percentage and an
amount with one concept name are two metric values, while the same figure in
millions and in billions stays one. The scale is used only to compare values
inside one key; the stored value and unit stay as printed.

The table lists the 22 unit strings the corpus's extract files hold, looked up
casefolded with whitespace runs collapsed. Any other string raises
UnknownUnit with its name, so a new unit is mapped here on purpose, never
guessed. Notes on the less obvious rows:
- "millions" alone appears only on Intel's dollar figures (cash flows, notional
  amounts), sometimes in conflict with "USD millions" for the same figure;
- "million PSUs" counts performance share units, which are share awards;
- "years" and "million square feet" are both `other`. They share a key only
  under one concept name for one subject and period, which no corpus figure does.

Two values agree when, after conversion to base units, they differ by no more
than half a unit in the last printed digit of the less precise one: $5.5
billion (5,450 to 5,550 million) and $5,473 million agree. The last printed
digit is read from the value as the model gave it, so "5.0 billion" counts as
"5 billion".
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType


class UnitClass(StrEnum):
    """The kind of quantity a metric value measures (CONTEXT.md); part of its key."""

    CURRENCY = "currency"
    PERCENT = "percent"
    PER_SHARE = "per_share"
    SHARES = "shares"
    COUNT = "count"
    OTHER = "other"


class UnknownUnit(ValueError):
    """A printed unit string the unit table does not list."""


@dataclass(frozen=True, slots=True)
class Unit:
    """A printed unit string's class and scale."""

    unit_class: UnitClass
    scale: Decimal  # base units per printed unit: USD billions is 10**9 USD


_THOUSAND, _MILLION, _BILLION = Decimal(10**3), Decimal(10**6), Decimal(10**9)
_ONE = Decimal(1)

_TABLE: dict[str, tuple[UnitClass, Decimal]] = {
    "usd": (UnitClass.CURRENCY, _ONE),
    "usd millions": (UnitClass.CURRENCY, _MILLION),
    "millions": (UnitClass.CURRENCY, _MILLION),
    "usd billions": (UnitClass.CURRENCY, _BILLION),
    "percent": (UnitClass.PERCENT, _ONE),
    "percent of revenue": (UnitClass.PERCENT, _ONE),
    "usd per share": (UnitClass.PER_SHARE, _ONE),
    "usd per diluted share": (UnitClass.PER_SHARE, _ONE),
    "shares": (UnitClass.SHARES, _ONE),
    "thousand shares": (UnitClass.SHARES, _THOUSAND),
    "million shares": (UnitClass.SHARES, _MILLION),
    "millions of shares": (UnitClass.SHARES, _MILLION),
    "shares millions": (UnitClass.SHARES, _MILLION),
    "shares in millions": (UnitClass.SHARES, _MILLION),
    "million psus": (UnitClass.SHARES, _MILLION),
    "billion shares": (UnitClass.SHARES, _BILLION),
    "shares billions": (UnitClass.SHARES, _BILLION),
    "employees": (UnitClass.COUNT, _ONE),
    "people": (UnitClass.COUNT, _ONE),
    "patents": (UnitClass.COUNT, _ONE),
    "years": (UnitClass.OTHER, _ONE),
    "million square feet": (UnitClass.OTHER, _MILLION),
}
UNITS = MappingProxyType({printed: Unit(*row) for printed, row in _TABLE.items()})


def unit_of(printed: str) -> Unit:
    """The class and scale of *printed*; UnknownUnit names a string the table lacks."""
    unit = UNITS.get(" ".join(printed.casefold().split()))
    if unit is None:
        raise UnknownUnit(f"unit {printed!r} is not in the unit table (extract/units.py)")
    return unit


def resolution(value: float, unit: str) -> Decimal:
    """One unit in the last printed digit of *value*, in base units."""
    printed = Decimal(repr(float(value)))
    places = 0 if printed == printed.to_integral_value() else printed.as_tuple().exponent
    return _ONE.scaleb(places) * unit_of(unit).scale


def agree(value_a: float, unit_a: str, value_b: float, unit_b: str) -> bool:
    """True when the two printed figures are one figure, within the coarser one's rounding."""
    if unit_of(unit_a).unit_class != unit_of(unit_b).unit_class:
        return False
    gap = abs(_base(value_a, unit_a) - _base(value_b, unit_b))
    return gap <= max(resolution(value_a, unit_a), resolution(value_b, unit_b)) / 2


def _base(value: float, unit: str) -> Decimal:
    """*value* in base units: 5.5 USD billions is 5,500,000,000."""
    return Decimal(repr(float(value))) * unit_of(unit).scale
