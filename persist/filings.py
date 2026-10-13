"""Which earlier 10-K each filing's risk factors link to (Step 10).

A 10-K links to the company's previous 10-K, and a 10-Q to the most recent
10-K before it, so annual changes are one hop and each quarterly update links
to the list it amends. "Before" is by report date. Only filings in the corpus
count: a filing whose previous 10-K is outside it gets no pair, and a 10-Q
never links to another 10-Q.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

TEN_K = "10-K"


@dataclass(frozen=True, slots=True)
class Filing:
    """A corpus filing as pairing needs it; `company` is its CIK or ticker."""

    accession_no: str
    company: str
    form_type: str
    report_date: date


@dataclass(frozen=True, slots=True)
class FilingPair:
    """A later filing and the earlier 10-K its risk factors link to."""

    later: Filing
    earlier: Filing


def filing_pairs(filings: Sequence[Filing]) -> tuple[FilingPair, ...]:
    """The pair of every filing with an earlier 10-K of its company, by company and date."""
    counts = Counter(f.accession_no for f in filings)
    if repeated := sorted(a for a, n in counts.items() if n > 1):
        raise ValueError(f"accession {repeated[0]} is listed {counts[repeated[0]]} times")
    ordered = sorted(filings, key=lambda f: (f.company, f.report_date, f.accession_no))
    pairs = []
    for later in ordered:
        earlier = [f for f in ordered if f.company == later.company and f.form_type == TEN_K
                   and f.report_date < later.report_date]
        if earlier:
            pairs.append(FilingPair(later=later, earlier=earlier[-1]))
    return tuple(pairs)
