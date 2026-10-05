"""The question filter parser of ADR-0001 (Step 5, ticket 04).

Every test calls the public parser with real question wording and the real
corpus filing list, and checks the filter a retrieval arm would apply.
"""
import json
from pathlib import Path

import pytest

from eval.filter_report import excluded_gold
from eval.schema import load_records
from retrieve.question_filter import (
    CorpusFiling,
    QuestionFilter,
    companies_without_chunks,
    parse_question_filter,
)
from tests.corpus_filings import FILING_LIST, corpus_filings

REPO = Path(__file__).resolve().parents[1]
FILINGS = corpus_filings()
CORPUS_FILINGS = 24  # CLAUDE.md corpus manifest: 2 fiscal years x 4 filings x 3 tickers
BY_ACCESSION = {f.accession_no: f for f in FILINGS}


def _parse(question: str) -> QuestionFilter:
    return parse_question_filter(question, FILINGS)


def _selected(f: QuestionFilter) -> set[tuple[str, str, str]]:
    """The filter's filings as (ticker, form, fiscal period)."""
    assert f.accession_nos is not None, "expected a filter"
    return {
        (BY_ACCESSION[a].ticker, BY_ACCESSION[a].form_type, BY_ACCESSION[a].fiscal_period)
        for a in f.accession_nos
    }


def _year(ticker: str, year: int) -> set[tuple[str, str, str]]:
    return {(ticker, "10-K", f"FY{year}")} | {
        (ticker, "10-Q", f"FY{year}-Q{q}") for q in (1, 2, 3)
    }


# ── the filing list ───────────────────────────────────────────────────────────

def test_the_filing_list_holds_the_24_corpus_filings():
    assert len(FILINGS) == CORPUS_FILINGS
    assert {f.ticker for f in FILINGS} == {"NVDA", "AMD", "INTC"}


def test_the_filing_list_matches_data_parsed_when_present():
    parsed = REPO / "data" / "parsed"
    if not any(parsed.glob("*.json")):
        pytest.skip("data/parsed is not present (CI)")
    from scripts.build_fixtures import filing_list_rows

    assert filing_list_rows(parsed) == json.loads(FILING_LIST.read_text(encoding="utf-8"))


# ── no filter ─────────────────────────────────────────────────────────────────

def test_a_question_with_no_company_period_or_form_has_no_filter():
    f = _parse("What do the filings say about supply chain risk?")
    assert f.accession_nos is None
    assert (f.companies, f.periods, f.forms) == ((), (), ())


def test_a_company_alone_filters_to_all_of_its_filings():
    f = _parse("What price per wafer does NVIDIA pay TSMC for its leading-edge GPUs?")
    assert f.companies == ("NVDA",)
    assert f.periods == ()
    assert _selected(f) == _year("NVDA", 2025) | _year("NVDA", 2026)


@pytest.mark.parametrize("name, ticker", [
    ("NVIDIA's", "NVDA"), ("Nvidia", "NVDA"), ("NVDA", "NVDA"),
    ("AMD's", "AMD"), ("Advanced Micro Devices", "AMD"),
    ("Intel's", "INTC"), ("INTC", "INTC"),
])
def test_company_names(name, ticker):
    assert _parse(f"What did {name} say about tariffs?").companies == (ticker,)


def test_words_containing_intel_are_not_intel():
    assert _parse("How is artificial intelligence changing demand?").accession_nos is None


# ── fiscal years ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", ["fiscal 2025", "FY2025", "FY 2025", "fiscal year 2025"])
def test_a_fiscal_year_is_its_10k_and_10qs(phrase):
    f = _parse(f"What drove AMD's Data Center revenue in {phrase}?")
    assert f.periods == ("FY2025",)
    assert _selected(f) == _year("AMD", 2025)


def test_a_fiscal_year_applies_to_nvidia_as_its_own_fiscal_year():
    f = _parse("By how much did NVIDIA's Data Center revenue grow in fiscal year 2025?")
    assert _selected(f) == _year("NVDA", 2025)


def test_a_fiscal_year_outside_the_corpus_sets_no_period():
    f = _parse("What was Intel's revenue in fiscal 2021?")
    assert f.periods == ()
    assert _selected(f) == _year("INTC", 2024) | _year("INTC", 2025)


# ── fiscal quarters ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "the first quarter of fiscal 2025",
    "the first quarter of fiscal year 2025",
    "its first-quarter fiscal 2025 10-Q",
    "Q1 fiscal 2025",
    "Q1 FY2025",
])
def test_a_fiscal_quarter_is_that_quarters_10q_only(phrase):
    f = _parse(f"What was AMD's gross margin in {phrase}?")
    assert f.periods == ("FY2025-Q1",)
    assert _selected(f) == {("AMD", "10-Q", "FY2025-Q1")}


def test_a_fiscal_quarter_applies_to_nvidia():
    f = _parse("In the third quarter of NVIDIA's fiscal year 2025, what share of Data Center "
               "revenue came from cloud service providers?")
    assert _selected(f) == {("NVDA", "10-Q", "FY2025-Q3")}


def test_a_fourth_quarter_has_no_10q_and_sets_no_period():
    f = _parse("What was Intel's revenue in the fourth quarter of fiscal 2025?")
    assert f.periods == ()
    assert _selected(f) == _year("INTC", 2024) | _year("INTC", 2025)


# ── calendar quarters ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker, name", [("AMD", "AMD"), ("INTC", "Intel")])
@pytest.mark.parametrize("phrase", [
    "Q3 2025", "the third quarter of 2025", "the third-quarter 2025 10-Q",
])
def test_a_calendar_quarter_filters_amd_and_intel(ticker, name, phrase):
    f = _parse(f"What did {name} report in {phrase}?")
    assert f.periods == ("FY2025-Q3",)
    assert _selected(f) == {(ticker, "10-Q", "FY2025-Q3")}


@pytest.mark.parametrize("phrase", ["Q3 2025", "the third quarter of 2025"])
def test_a_calendar_quarter_sets_nothing_for_nvidia(phrase):
    f = _parse(f"What did NVIDIA report in {phrase}?")
    assert f.periods == ()
    assert _selected(f) == _year("NVDA", 2025) | _year("NVDA", 2026)


def test_a_calendar_quarter_with_nvidia_and_amd_filters_only_amd():
    f = _parse("How did NVIDIA and AMD describe export controls in Q2 2025?")
    assert f.periods == ("FY2025-Q2",)
    assert _selected(f) == (
        _year("NVDA", 2025) | _year("NVDA", 2026) | {("AMD", "10-Q", "FY2025-Q2")}
    )


# ── bare years and dates ──────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "What were the key terms of Intel's August 2025 agreement with the Department of Commerce?",
    "How did Intel's 2025 restructuring change its headcount?",
    "What were Intel's inventories at March 29, 2025?",
])
def test_a_bare_year_or_date_sets_no_period(question):
    f = _parse(question)
    assert f.periods == ()
    assert _selected(f) == _year("INTC", 2024) | _year("INTC", 2025)


# ── form types ────────────────────────────────────────────────────────────────

def test_a_form_name_restricts_the_form():
    f = _parse("What does Intel's 10-K say about foundry customers?")
    assert f.forms == ("10-K",)
    assert _selected(f) == {("INTC", "10-K", "FY2024"), ("INTC", "10-K", "FY2025")}


def test_a_plural_form_name_counts():
    f = _parse("Which of NVIDIA, AMD or Intel is the best stock, according to their 10-Ks?")
    assert f.forms == ("10-K",)
    assert {form for _, form, _ in _selected(f)} == {"10-K"}


def test_a_form_and_a_fiscal_year_intersect():
    f = _parse("According to NVIDIA's FY2026 10-K, how have export controls affected demand?")
    assert _selected(f) == {("NVDA", "10-K", "FY2026")}


# ── latest ────────────────────────────────────────────────────────────────────

def test_latest_next_to_a_form_is_each_companys_newest_filing_of_that_form():
    f = _parse("How do NVIDIA, AMD and Intel describe climate risk in their latest 10-Ks?")
    assert _selected(f) == {
        ("NVDA", "10-K", "FY2026"), ("AMD", "10-K", "FY2025"), ("INTC", "10-K", "FY2025"),
    }
    assert f.periods == ("FY2025", "FY2026")


def test_latest_with_no_company_named_covers_every_company():
    f = _parse("How did AI demand show up in each company's segment results in its latest 10-K?")
    assert f.companies == ()
    assert len(_selected(f)) == 3


def test_most_recent_10q():
    f = _parse("What did AMD disclose in its most recent 10-Q?")
    assert _selected(f) == {("AMD", "10-Q", "FY2025-Q3")}


def test_latest_without_a_form_sets_no_period():
    f = _parse("What are AMD's latest annual reports saying about AI?")
    assert f.periods == ()
    assert _selected(f) == _year("AMD", 2024) | _year("AMD", 2025)


# ── unions ────────────────────────────────────────────────────────────────────

def test_several_companies_and_periods_filter_to_their_union():
    f = _parse("In calendar 2024, how did data center revenue growth compare between AMD "
               "(fiscal 2024) and NVIDIA (fiscal 2025)?")
    assert f.companies == ("NVDA", "AMD")
    assert f.periods == ("FY2024", "FY2025")
    assert _selected(f) == _year("AMD", 2024) | _year("AMD", 2025) | _year("NVDA", 2025)


def test_several_quarters_filter_to_their_union():
    f = _parse("How did Intel's DCAI revenue in the second quarter of 2025 compare with the "
               "second quarter of 2024?")
    assert _selected(f) == {("INTC", "10-Q", "FY2024-Q2"), ("INTC", "10-Q", "FY2025-Q2")}


def test_a_fiscal_year_and_a_quarter_filter_to_their_union():
    f = _parse("AMD's fiscal 2024 10-K described its restructuring plan. What did the "
               "first-quarter 2025 10-Q then report?")
    assert f.periods == ("FY2024", "FY2025-Q1")
    assert _selected(f) == _year("AMD", 2024) | {("AMD", "10-Q", "FY2025-Q1")}


@pytest.mark.parametrize("question", [
    "What was AMD's revenue in fiscal 2024 and 2025?",
    "How did AMD's margins change between fiscal years 2024 and 2025?",
    "What was AMD's gross margin in Q1 and Q2 of fiscal 2025?",
    "What did AMD report in the first and second quarters of 2025?",
    "How did AMD's Embedded revenue move across the third quarters of 2024 and 2025?",
])
def test_a_list_sharing_one_year_or_quarter_word_sets_no_period(question):
    # Reading only one item of the list would cut out the others' evidence.
    f = _parse(question)
    assert f.periods == ()
    assert _selected(f) == _year("AMD", 2024) | _year("AMD", 2025)


def test_a_list_of_complete_period_phrases_still_filters():
    f = _parse("How did AMD explain the change between its Q3 2024 and Q3 2025 10-Qs?")
    assert _selected(f) == {("AMD", "10-Q", "FY2024-Q3"), ("AMD", "10-Q", "FY2025-Q3")}


def test_a_fourth_quarter_beside_another_period_leaves_the_company_unfiltered():
    # Q4 has no 10-Q; filtering to Q1 alone would drop the 10-K that covers Q4.
    f = _parse("How did Intel's Q4 2024 results compare with Q1 2025?")
    assert f.periods == ()
    assert _selected(f) == _year("INTC", 2024) | _year("INTC", 2025)


# ── companies without chunks ──────────────────────────────────────────────────

def test_companies_without_chunks_names_the_companies_with_no_retrieved_chunk():
    f = _parse("How do NVIDIA, AMD and Intel describe TSMC?")
    nvda = next(a.accession_no for a in FILINGS if a.ticker == "NVDA")
    assert companies_without_chunks(f, [f"{nvda}:0001"], FILINGS) == ("AMD", "INTC")


def test_companies_without_chunks_is_empty_for_a_single_company_question():
    f = _parse("What did AMD say about tariffs?")
    assert companies_without_chunks(f, [], FILINGS) == ()


def test_a_named_company_with_no_filing_in_the_list_sets_no_filter():
    nvidia_only = tuple(f for f in FILINGS if f.ticker == "NVDA")
    f = parse_question_filter("What did AMD say about tariffs in fiscal 2025?", nvidia_only)
    assert f.companies == ("AMD",)
    assert f.accession_nos is None


# ── input checks ──────────────────────────────────────────────────────────────

def test_an_empty_filing_list_is_refused():
    with pytest.raises(ValueError, match="filing list is empty"):
        parse_question_filter("What did AMD report?", ())


def test_an_unknown_ticker_in_the_filing_list_is_refused():
    bad = (*FILINGS[:1], CorpusFiling("0000000001-25-000001", "TSLA", "10-K", "FY2025",
                                      FILINGS[0].report_date))
    with pytest.raises(ValueError, match="TSLA"):
        parse_question_filter("What did AMD report?", bad)


# ── the dev records ───────────────────────────────────────────────────────────

def test_the_filter_excludes_no_dev_gold_chunk():
    records = load_records(REPO / "eval" / "agent_drafted_set.jsonl")
    dev = [r for r in records if r.split == "dev"]
    assert len(dev) > 80
    excluded = {r.id: excluded_gold(_parse(r.question), r) for r in dev}
    assert {qid: ids for qid, ids in excluded.items() if ids} == {}
