"""The question filter of ADR-0001: companies, periods and forms read from the question text.

One deterministic parser shared by every arm.  It reads only the question and
the corpus's filing list, which the caller passes in, so it never sees an eval
record's labels and never queries the database.

The rules:

- Company names restrict the companies.  With none named, every company is in scope.
- "Fiscal 2025", "FY2025" and "fiscal year 2025" select that fiscal year's 10-K
  and 10-Qs.  "The first quarter of fiscal 2025", "Q1 fiscal 2025" and "Q1 FY2025"
  select that quarter's 10-Q.  Both apply to every company.
- Calendar quarters ("Q3 2025", "the third quarter of 2025") apply only to AMD and
  Intel, whose fiscal years follow the calendar.  For NVIDIA they set nothing.
- A bare year ("August 2025", "2025 restructuring") sets nothing.
- "10-K" or "10-Q" restricts the form.  "Latest" or "most recent" directly before
  a form name selects each company's newest filing of that form.
- Several companies or periods select their union.  A list that shares one year
  or quarter word ("fiscal 2024 and 2025", "Q1 and Q2 of fiscal 2025") sets no
  period, because reading one item would cut out the others.
- When unsure it filters less, not more: a period with no filing in the corpus
  is ignored, a fourth quarter (which has no 10-Q) leaves its company with no
  period filter, a company left with no filing keeps all its filings, and a
  filter that selects nothing is no filter.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from ingest.chunker import accession_of

TICKERS = ("NVDA", "AMD", "INTC")  # the order companies are reported in
CALENDAR_YEAR_TICKERS = frozenset({"AMD", "INTC"})  # fiscal quarters match calendar quarters
FORMS = ("10-K", "10-Q")
MIN_COMPANIES_FOR_COVERAGE = 2  # companies_without_chunks reports multi-company questions only

_FISCAL_PERIOD = re.compile(r"FY\d{4}(-Q[1-4])?")
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_ORD_WORD = r"(?P<q>first|second|third|fourth)"
_Q_DIGIT = r"Q(?P<q>[1-4])"
_YEAR = r"(?P<y>\d{4})"
_POSSESSIVE = r"(?:(?:the|its|their|[a-z][\w.&]*'s)\s+)?"  # "of NVIDIA's fiscal year 2025"
_FISCAL = r"(?:fiscal(?:[\s-]+year)?\s*|FY\s?-?)"
_FLAGS = re.IGNORECASE

_FISCAL_QUARTERS = tuple(re.compile(p, _FLAGS) for p in (
    rf"\b{_ORD_WORD}[\s-]+quarter\s+(?:of\s+)?{_POSSESSIVE}{_FISCAL}{_YEAR}\b",
    rf"\b{_Q_DIGIT}\s+(?:of\s+)?{_POSSESSIVE}{_FISCAL}{_YEAR}\b",
    rf"\bFY\s?-?{_YEAR}\s*-?\s*{_Q_DIGIT}\b",
))
_CALENDAR_QUARTERS = tuple(re.compile(p, _FLAGS) for p in (
    rf"\b{_Q_DIGIT}\s+(?:of\s+)?{_YEAR}\b",
    rf"\b{_ORD_WORD}[\s-]+quarters?\s+(?:of\s+)?(?:calendar\s+(?:year\s+)?)?{_YEAR}\b",
))
_FISCAL_YEARS = tuple(re.compile(p, _FLAGS) for p in (
    rf"\bfiscal(?:[\s-]+years?)?\s*{_YEAR}\b",
    rf"\bFY\s?-?{_YEAR}\b",
))
_COMPANIES = {
    "NVDA": re.compile(r"\b(?:nvidia|nvda)\b", _FLAGS),
    "AMD": re.compile(r"\b(?:amd|advanced\s+micro\s+devices)\b", _FLAGS),
    "INTC": re.compile(r"\b(?:intel|intc)\b", _FLAGS),
}
_CONJ = r"\s*(?:,\s*(?:and\s+|or\s+)?|and\s+|or\s+|to\s+|through\s+|[-\u2013]\s*)"
_QUARTER_WORD = r"(?:Q[1-4]|first|second|third|fourth)"
_PERIOD_LISTS = tuple(re.compile(p, _FLAGS) for p in (
    rf"\b\d{{4}}{_CONJ}\d{{4}}\b",  # "fiscal 2024 and 2025"
    rf"\b{_QUARTER_WORD}{_CONJ}{_QUARTER_WORD}\b",  # "Q1 and Q2 of fiscal 2025"
))
_FOURTH_QUARTER = 4
_FORM = re.compile(r"\b10-?(?P<f>[KQ])s?\b", _FLAGS)
_LATEST_FORM = re.compile(r"\b(?:latest|most\s+recent)\s+10-?(?P<f>[KQ])s?\b", _FLAGS)


@dataclass(frozen=True)
class CorpusFiling:
    """One filing in the corpus, as the parser needs it."""

    accession_no: str
    ticker: str
    form_type: str
    fiscal_period: str  # "FY2025" (10-K) or "FY2025-Q3" (10-Q)
    report_date: date

    @property
    def fiscal_year(self) -> int:
        return int(self.fiscal_period[2:6])


@dataclass(frozen=True)
class QuestionFilter:
    """What the question names, and the filings retrieval may draw from."""

    companies: tuple[str, ...]  # named in the question, in TICKERS order
    periods: tuple[str, ...]  # fiscal periods that selected a filing, sorted
    forms: tuple[str, ...]  # form types named, sorted
    accession_nos: tuple[str, ...] | None  # sorted; None when every filing passes

    def as_dict(self) -> dict[str, object]:
        return {
            "companies": list(self.companies),
            "periods": list(self.periods),
            "forms": list(self.forms),
            "accession_nos": None if self.accession_nos is None else list(self.accession_nos),
        }


@dataclass(frozen=True)
class _Phrases:
    """The filter phrases found in one question."""

    companies: frozenset[str]
    fiscal_years: frozenset[int]
    fiscal_quarters: frozenset[tuple[int, int]]  # (fiscal year, quarter)
    calendar_quarters: frozenset[tuple[int, int]]  # (calendar year, quarter)
    forms: frozenset[str]
    latest_forms: frozenset[str]


def parse_question_filter(question: str, filings: Sequence[CorpusFiling]) -> QuestionFilter:
    """The filter for *question* over *filings*; raises ``ValueError`` on a bad filing list."""
    check_filings(filings)
    phrases = _read_phrases(question)
    in_scope = phrases.companies or frozenset(f.ticker for f in filings)
    resolved = [
        _resolve_company(phrases, ticker, [f for f in filings if f.ticker == ticker])
        for ticker in in_scope
    ]
    selected = frozenset().union(*(chosen for chosen, _ in resolved))
    periods = frozenset().union(*(labels for _, labels in resolved))
    no_filter = not selected or len(selected) == len(filings)  # nothing named is in the corpus
    return QuestionFilter(
        companies=tuple(t for t in TICKERS if t in phrases.companies),
        periods=tuple(sorted(periods)),
        forms=tuple(sorted(phrases.forms)),
        accession_nos=None if no_filter else tuple(sorted(f.accession_no for f in selected)),
    )


def companies_without_chunks(
    question_filter: QuestionFilter, chunk_ids: Iterable[str], filings: Sequence[CorpusFiling]
) -> tuple[str, ...]:
    """Companies a multi-company question names that no retrieved chunk comes from."""
    if len(question_filter.companies) < MIN_COMPANIES_FOR_COVERAGE:
        return ()
    ticker_of = {f.accession_no: f.ticker for f in filings}
    retrieved = {ticker_of.get(accession_of(chunk_id)) for chunk_id in chunk_ids}
    return tuple(c for c in question_filter.companies if c not in retrieved)


def check_filings(filings: Sequence[CorpusFiling]) -> None:
    """Raise ``ValueError`` unless *filings* is a usable filing list."""
    if not filings:
        raise ValueError("the filing list is empty")
    accessions = [f.accession_no for f in filings]
    if len(set(accessions)) != len(accessions):
        raise ValueError("the filing list repeats an accession number")
    for f in filings:
        if f.ticker not in TICKERS or f.form_type not in FORMS:
            raise ValueError(f"{f.accession_no}: unknown ticker or form {f.ticker} {f.form_type}")
        if not _FISCAL_PERIOD.fullmatch(f.fiscal_period):
            raise ValueError(f"{f.accession_no}: bad fiscal period {f.fiscal_period!r}")


def _read_phrases(question: str) -> _Phrases:
    """Find every filter phrase.  Quarter phrases are blanked once read, so the
    "fiscal 2025" inside "the first quarter of fiscal 2025" is not also a fiscal year."""
    original = question.replace("\u2019", "'")
    unsure = any(pattern.search(original) for pattern in _PERIOD_LISTS)
    fiscal_quarters, text = _take(_FISCAL_QUARTERS, original)
    calendar_quarters, text = _take(_CALENDAR_QUARTERS, text)
    fiscal_years, _ = _take(_FISCAL_YEARS, text)
    if unsure:
        fiscal_quarters, calendar_quarters, fiscal_years = [], [], []
    return _Phrases(
        companies=frozenset(t for t, pattern in _COMPANIES.items() if pattern.search(original)),
        fiscal_years=frozenset(int(m["y"]) for m in fiscal_years),
        fiscal_quarters=frozenset(map(_year_quarter, fiscal_quarters)),
        calendar_quarters=frozenset(map(_year_quarter, calendar_quarters)),
        forms=frozenset(f"10-{m['f'].upper()}" for m in _FORM.finditer(original)),
        latest_forms=frozenset(f"10-{m['f'].upper()}" for m in _LATEST_FORM.finditer(original)),
    )


def _take(
    patterns: Sequence[re.Pattern[str]], text: str
) -> tuple[list[re.Match[str]], str]:
    """Every match of *patterns*, and *text* with the matches blanked to spaces."""
    matches: list[re.Match[str]] = []
    for pattern in patterns:
        matches += pattern.finditer(text)
        text = pattern.sub(lambda m: " " * len(m[0]), text)
    return matches, text


def _year_quarter(match: re.Match[str]) -> tuple[int, int]:
    quarter = match["q"]
    return int(match["y"]), _ORDINALS.get(quarter.lower()) or int(quarter)


def _resolve_company(
    phrases: _Phrases, ticker: str, own: Sequence[CorpusFiling]
) -> tuple[frozenset[CorpusFiling], frozenset[str]]:
    """The company's filings (*own*) the phrases select, and the periods that selected them."""
    def form_ok(f: CorpusFiling) -> bool:
        return not phrases.forms or f.form_type in phrases.forms

    picks = [
        (label, frozenset(f for f in chosen if form_ok(f)))
        for label, chosen in _period_picks(phrases, ticker, own)
    ]
    picks = [(label, chosen) for label, chosen in picks if chosen]
    fourth = any(q == _FOURTH_QUARTER for _, q in _quarters_for(phrases, ticker))
    if fourth or not picks:  # no usable period phrase for this company
        return frozenset(f for f in own if form_ok(f)), frozenset()
    return frozenset().union(*(c for _, c in picks)), frozenset(label for label, _ in picks)


def _period_picks(
    phrases: _Phrases, ticker: str, own: Sequence[CorpusFiling]
) -> list[tuple[str, frozenset[CorpusFiling]]]:
    """(period label, filings) for each period phrase that applies to *ticker*."""
    quarters = _quarters_for(phrases, ticker)
    newest = [
        max((f for f in own if f.form_type == form), key=lambda f: f.report_date, default=None)
        for form in phrases.latest_forms
    ]
    return [
        (f"FY{year}", frozenset(f for f in own if f.fiscal_year == year))
        for year in phrases.fiscal_years
    ] + [
        (f"FY{year}-Q{q}", frozenset(f for f in own if f.fiscal_period == f"FY{year}-Q{q}"))
        for year, q in quarters
    ] + [(f.fiscal_period, frozenset({f})) for f in newest if f is not None]


def _quarters_for(phrases: _Phrases, ticker: str) -> frozenset[tuple[int, int]]:
    """Fiscal quarters for every company; calendar quarters only where they coincide."""
    calendar = phrases.calendar_quarters if ticker in CALENDAR_YEAR_TICKERS else frozenset()
    return phrases.fiscal_quarters | calendar
