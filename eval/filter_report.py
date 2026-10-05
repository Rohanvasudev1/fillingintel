"""How the question filter did against the eval labels (ADR-0001, Step 5).

The arms never see the labels; only the harness compares them, after the run.
Exact match means the filter's companies equal the record's ``tickers`` and its
periods equal the record's ``fiscal_periods``.  Filter-excluded gold counts gold
chunks whose filing the filter shut out; the baseline run needs it to be 0.
"""
from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean
from typing import TYPE_CHECKING

from eval.schema import EvalRecord
from ingest.chunker import accession_of
from retrieve.question_filter import QuestionFilter

if TYPE_CHECKING:
    from eval.results import Outcome

DEFINITIONS = {
    "exact_match": "share of questions whose filter companies and periods both equal the labels",
    "companies_match": "share of questions whose filter companies equal the tickers label",
    "periods_match": "share of questions whose filter periods equal the fiscal_periods label",
    "filter_excluded_gold": "gold chunks whose filing the filter excluded, summed over questions",
}


def companies_match(question_filter: QuestionFilter, record: EvalRecord) -> bool:
    return set(question_filter.companies) == set(record.tickers)


def periods_match(question_filter: QuestionFilter, record: EvalRecord) -> bool:
    return set(question_filter.periods) == set(record.fiscal_periods)


def exact_match(question_filter: QuestionFilter, record: EvalRecord) -> bool:
    return companies_match(question_filter, record) and periods_match(question_filter, record)


def excluded_gold(question_filter: QuestionFilter, record: EvalRecord) -> list[str]:
    """The record's gold chunk IDs whose filing is outside the filter."""
    if question_filter.accession_nos is None:
        return []
    allowed = set(question_filter.accession_nos)
    return [g for g in record.gold_chunk_ids if accession_of(g) not in allowed]


def _set_report(outcomes: Sequence[Outcome]) -> dict[str, object]:
    pairs = [(o.result.question_filter, o.record) for o in outcomes]
    lost = {r.id: excluded_gold(f, r) for f, r in pairs}
    return {
        "n": len(pairs),
        "exact_match": fmean(exact_match(f, r) for f, r in pairs),
        "companies_match": fmean(companies_match(f, r) for f, r in pairs),
        "periods_match": fmean(periods_match(f, r) for f, r in pairs),
        "filter_excluded_gold": sum(len(ids) for ids in lost.values()),
        "questions_with_excluded_gold": [qid for qid, ids in lost.items() if ids],
    }


def build_filter_report(outcomes: Sequence[Outcome]) -> dict[str, dict[str, object]]:
    """``filter_report[question_set]``: exact match and filter-excluded gold."""
    return {
        set_name: _set_report([o for o in outcomes if o.question_set == set_name])
        for set_name in dict.fromkeys(o.question_set for o in outcomes)
    }
