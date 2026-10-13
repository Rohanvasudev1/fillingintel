"""The hand-written unit table: each printed unit string to a unit class and a scale (Step 10).

The table covers the 22 unit strings the corpus's extract files hold. A unit
string it does not list fails with its name, so a new unit is mapped on
purpose, never guessed.
"""
from decimal import Decimal

import pytest

from extract.units import UnitClass, UnknownUnit, agree, resolution, unit_of

CORPUS_UNITS = {
    "USD": (UnitClass.CURRENCY, 1),
    "USD millions": (UnitClass.CURRENCY, 10**6),
    "millions": (UnitClass.CURRENCY, 10**6),
    "USD billions": (UnitClass.CURRENCY, 10**9),
    "percent": (UnitClass.PERCENT, 1),
    "percent of revenue": (UnitClass.PERCENT, 1),
    "USD per share": (UnitClass.PER_SHARE, 1),
    "USD per diluted share": (UnitClass.PER_SHARE, 1),
    "shares": (UnitClass.SHARES, 1),
    "thousand shares": (UnitClass.SHARES, 10**3),
    "million shares": (UnitClass.SHARES, 10**6),
    "millions of shares": (UnitClass.SHARES, 10**6),
    "shares millions": (UnitClass.SHARES, 10**6),
    "shares in millions": (UnitClass.SHARES, 10**6),
    "million PSUs": (UnitClass.SHARES, 10**6),
    "billion shares": (UnitClass.SHARES, 10**9),
    "shares billions": (UnitClass.SHARES, 10**9),
    "employees": (UnitClass.COUNT, 1),
    "people": (UnitClass.COUNT, 1),
    "patents": (UnitClass.COUNT, 1),
    "years": (UnitClass.OTHER, 1),
    "million square feet": (UnitClass.OTHER, 10**6),
}


def test_the_table_covers_the_22_corpus_unit_strings():
    assert len(CORPUS_UNITS) == 22


@pytest.mark.parametrize(("printed", "expected"), CORPUS_UNITS.items())
def test_each_corpus_unit_has_its_class_and_scale(printed, expected):
    unit = unit_of(printed)
    assert (unit.unit_class, unit.scale) == (expected[0], Decimal(expected[1]))


def test_lookup_ignores_case_and_spacing():
    assert unit_of("  usd   Millions ") == unit_of("USD millions")


def test_an_unknown_unit_fails_with_its_name():
    with pytest.raises(UnknownUnit, match="'USD trillions'"):
        unit_of("USD trillions")


@pytest.mark.parametrize(("a", "b"), [
    ((5.5, "USD billions"), (5473.0, "USD millions")),  # AMD FY2024-Q1 net revenue
    ((21.4, "USD billions"), (21383.0, "USD millions")),  # NVIDIA FY2026 income tax
    ((5.55, "USD billions"), (5.5, "USD billions")),  # exactly half a printed unit apart
    ((55.6, "percent"), (55.6, "percent of revenue")),
])
def test_figures_within_the_coarser_ones_rounding_agree(a, b):
    assert agree(*a, *b) and agree(*b, *a)


@pytest.mark.parametrize(("a", "b"), [
    ((5.6, "USD billions"), (5473.0, "USD millions")),
    ((21383.0, "USD millions"), (21384.0, "USD millions")),
    ((55.6, "percent"), (55.6, "USD")),  # different unit classes never agree
])
def test_figures_outside_the_rounding_disagree(a, b):
    assert not agree(*a, *b)


def test_resolution_is_one_unit_in_the_last_printed_digit():
    assert resolution(5.5, "USD billions") == Decimal(10**8)
    assert resolution(5473.0, "USD millions") == Decimal(10**6)
    assert resolution(0.25, "USD per share") == Decimal("0.01")
