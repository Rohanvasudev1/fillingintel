"""filing_pairs(): which earlier 10-K each filing's risk factors link to (Step 10)."""
from datetime import date

import pytest

from persist.filings import Filing, filing_pairs
from tests.corpus_filings import corpus_filings

CORPUS = tuple(Filing(f.accession_no, f.ticker, f.form_type, f.report_date)
               for f in corpus_filings())
BY_ACCESSION = {f.accession_no: f for f in corpus_filings()}


def _periods(pairs):
    return sorted((BY_ACCESSION[p.later.accession_no].ticker,
                   BY_ACCESSION[p.later.accession_no].fiscal_period,
                   BY_ACCESSION[p.earlier.accession_no].fiscal_period) for p in pairs)


def test_the_corpus_has_twelve_pairs_four_per_company():
    pairs = filing_pairs(CORPUS)
    assert len(pairs) == 12
    assert _periods(pairs) == sorted(
        (ticker, later, earlier)
        for ticker, year, prior in (("AMD", 2025, 2024), ("INTC", 2025, 2024),
                                    ("NVDA", 2026, 2025))
        for later, earlier in ((f"FY{year}", f"FY{prior}"),
                               *((f"FY{year}-Q{q}", f"FY{prior}") for q in (1, 2, 3)))
    )


def test_every_earlier_filing_is_a_10k_of_the_same_company():
    for pair in filing_pairs(CORPUS):
        assert pair.earlier.form_type == "10-K"
        assert pair.earlier.company == pair.later.company
        assert pair.earlier.report_date < pair.later.report_date


def test_a_filing_with_no_earlier_10k_in_the_corpus_gets_no_pair():
    later = {p.later.accession_no for p in filing_pairs(CORPUS)}
    first_fiscal_year = {"AMD": "FY2024", "INTC": "FY2024", "NVDA": "FY2025"}
    first_year = {f.accession_no for f in corpus_filings()
                  if f.fiscal_period.startswith(first_fiscal_year[f.ticker])}
    assert len(first_year) == 12
    assert not later & first_year


def test_a_10q_links_to_the_most_recent_earlier_10k_not_an_older_one():
    filings = (
        Filing("0000000001-23-000001", "X", "10-K", date(2023, 1, 1)),
        Filing("0000000001-24-000001", "X", "10-K", date(2024, 1, 1)),
        Filing("0000000001-24-000002", "X", "10-Q", date(2024, 4, 1)),
    )
    pairs = filing_pairs(filings)
    assert [(p.later.accession_no, p.earlier.accession_no) for p in pairs] == [
        ("0000000001-24-000001", "0000000001-23-000001"),
        ("0000000001-24-000002", "0000000001-24-000001"),
    ]


def test_another_companys_10k_is_never_a_predecessor():
    filings = (
        Filing("0000000001-23-000001", "X", "10-K", date(2023, 1, 1)),
        Filing("0000000002-24-000001", "Y", "10-Q", date(2024, 4, 1)),
    )
    assert filing_pairs(filings) == ()


def test_a_repeated_accession_is_refused():
    filing = Filing("0000000001-23-000001", "X", "10-K", date(2023, 1, 1))
    with pytest.raises(ValueError, match="0000000001-23-000001"):
        filing_pairs((filing, filing))
