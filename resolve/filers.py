"""The hand-written filer-name table (Step 9).

A filer extracted as an Organization ("NVIDIA Corporation" in an AMD filing)
is the same entity as its Company node. Each name below is a deliberate
choice; an Organization whose match key equals one of theirs merges into that
Company by CIK, the only merge across labels. Subsidiaries ("Xilinx", "Altera",
"Mellanox") are not listed: whether a filing uses them interchangeably with the
parent is the adjudicator's call.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from extract.filers import FILER_CIKS
from resolve.groups import COMPANY_PREFIX
from resolve.normalize import match_key

ORGANIZATION = "Organization"
FILER_RULE = "filer_table"
# One name per match key: "NVIDIA Corporation" also covers "NVIDIA" and "NVIDIA Corp.".
FILER_NAMES = MappingProxyType({
    "NVDA": ("NVIDIA Corporation",),
    "AMD": ("Advanced Micro Devices, Inc.", "AMD"),
    "INTC": ("Intel Corporation",),
})


@dataclass(frozen=True, slots=True)
class FilerEntry:
    """One row of the table: the printed name and the filer it names."""

    name: str
    ticker: str
    cik: str

    @property
    def company_key(self) -> str:
        return company_key(self.cik)


def company_key(cik: str) -> str:
    """The key a filer's Company node has in the alias map."""
    return f"{COMPANY_PREFIX}{cik}"


def _table() -> MappingProxyType[str, FilerEntry]:
    table: dict[str, FilerEntry] = {}
    for ticker, names in FILER_NAMES.items():
        for name in names:
            entry = FilerEntry(name, ticker, FILER_CIKS[ticker])
            kept = table.setdefault(match_key(name), entry)
            if kept is not entry:
                raise ValueError(f"{name!r} and {kept.name!r} have the same match key")
    return MappingProxyType(table)


FILER_TABLE = _table()


def filer_entry(label: str, name: str) -> FilerEntry | None:
    """The table row an Organization's name matches, or None."""
    return FILER_TABLE.get(match_key(name)) if label == ORGANIZATION else None
