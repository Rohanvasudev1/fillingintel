"""Rebuild tests/fixtures/*.html.gz from data/raw/ and corpus_filings.json from data/parsed/.

The fixtures are the unmodified EDGAR HTML of six real filings (one 10-K and
one 10-Q per ticker), gzipped, plus the metadata of the 24 corpus filings
(no text), which the question filter tests need offline.  Run
``uv run python -m ingest.corpus`` first so data/raw/ and data/parsed/ are
populated.

    uv run python scripts/build_fixtures.py
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

RAW_DIR = Path("data/raw")
PARSED_DIR = Path("data/parsed")
FIXTURE_DIR = Path("tests/fixtures")
FILING_LIST = "corpus_filings.json"

FIXTURES: dict[str, str] = {
    "nvda_10k": "0001045810-26-000021",
    "nvda_10q": "0001045810-26-000075",
    "amd_10k": "0000002488-26-000018",
    "amd_10q": "0000002488-26-000123",
    "intc_10k": "0000050863-26-000011",
    "intc_10q": "0000050863-26-000157",
}


def build_fixture(raw_path: Path, out_path: Path) -> None:
    """Gzip *raw_path* to *out_path* deterministically (fixed mtime)."""
    out_path.write_bytes(gzip.compress(raw_path.read_bytes(), mtime=0))


def filing_list_rows(parsed_dir: Path) -> list[dict[str, str]]:
    """Each parsed filing's ticker, form, fiscal period and report date, by accession number."""
    from ingest.corpus import TICKER_BY_CIK
    from ingest.parsed_files import read_parsed_dir

    return [
        {
            "accession_no": r.meta.accession_no,
            "ticker": TICKER_BY_CIK[r.meta.cik],
            "form_type": r.meta.form_type,
            "fiscal_period": r.meta.fiscal_period,
            "report_date": r.meta.report_date.isoformat(),
        }
        for r in read_parsed_dir(parsed_dir)
    ]


def main() -> int:
    missing = [a for a in FIXTURES.values() if not (RAW_DIR / f"{a}.html").is_file()]
    if missing:
        print(f"Missing from {RAW_DIR}/: {', '.join(missing)}", file=sys.stderr)
        return 1
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, accession in FIXTURES.items():
        build_fixture(RAW_DIR / f"{accession}.html", FIXTURE_DIR / f"{name}.html.gz")
        print(f"{name}: {accession}")
    rows = filing_list_rows(PARSED_DIR)
    (FIXTURE_DIR / FILING_LIST).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"{FILING_LIST}: {len(rows)} filings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
