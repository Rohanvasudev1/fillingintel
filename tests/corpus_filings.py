"""The corpus's 24 filings as metadata, for tests that need the filing list offline.

``tests/fixtures/corpus_filings.json`` is built from ``data/parsed`` by
``scripts/build_fixtures.py``; ``test_question_filter.py`` checks it still
matches when ``data/parsed`` is present.
"""
import json
from datetime import date
from pathlib import Path

from retrieve.question_filter import CorpusFiling

FILING_LIST = Path(__file__).parent / "fixtures" / "corpus_filings.json"


def corpus_filings() -> tuple[CorpusFiling, ...]:
    rows = json.loads(FILING_LIST.read_text(encoding="utf-8"))
    return tuple(
        CorpusFiling(
            accession_no=r["accession_no"],
            ticker=r["ticker"],
            form_type=r["form_type"],
            fiscal_period=r["fiscal_period"],
            report_date=date.fromisoformat(r["report_date"]),
        )
        for r in rows
    )
