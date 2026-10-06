"""Rebuild the test fixtures from data/ and the local database.

The fixtures are the unmodified EDGAR HTML of six real filings (one 10-K and
one 10-Q per ticker), gzipped; the metadata of the 24 corpus filings (no
text), which the question filter tests need offline; and the corpus snapshot
the quality gate loads (eval/snapshot.py, ADR-0003), built from the local
database and query embedding cache.  Run ``ingest.corpus`` first for the HTML
and the filing list.  The snapshot also needs ``ingest.load``, ``ingest.embed``
and one ``eval.run`` on ``dev``, and is skipped when ``DATABASE_URL`` is unset.

    uv run --env-file .env python -m scripts.build_fixtures
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import zlib
from pathlib import Path

RAW_DIR = Path("data/raw")
PARSED_DIR = Path("data/parsed")
FIXTURE_DIR = Path("tests/fixtures")
FILING_LIST = "corpus_filings.json"
SNAPSHOT_FILE = "corpus_snapshot.jsonl.gz"

FIXTURES: dict[str, str] = {
    "nvda_10k": "0001045810-26-000021",
    "nvda_10q": "0001045810-26-000075",
    "amd_10k": "0000002488-26-000018",
    "amd_10q": "0000002488-26-000123",
    "intc_10k": "0000050863-26-000011",
    "intc_10q": "0000050863-26-000157",
}


def build_fixture(raw_path: Path, out_path: Path) -> None:
    """Gzip *raw_path* to *out_path* deterministically (fixed mtime).

    A fixture that already holds the same content is left as it is, so a rebuild
    does not rewrite committed files whose gzip header differs.
    """
    raw = raw_path.read_bytes()
    if out_path.is_file():
        try:
            if gzip.decompress(out_path.read_bytes()) == raw:
                return
        except (OSError, EOFError, zlib.error):
            print(f"{out_path.name} is damaged; rewriting it", file=sys.stderr)
    out_path.write_bytes(gzip.compress(raw, mtime=0))


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


def build_snapshot_file(url: str, out_path: Path) -> int:
    """Write the corpus snapshot from the database at *url*; returns its size in bytes."""
    import psycopg

    from eval.question_sets import agent_drafted_set, load_question_sets
    from eval.snapshot import build_snapshot, gated_questions, query_vectors_from_cache
    from ingest.voyage import DEFAULT_MODEL
    from retrieve.query_cache import DEFAULT_CACHE_DIR

    records = agent_drafted_set(load_question_sets()).records
    questions = gated_questions(records)
    vectors = query_vectors_from_cache(questions, DEFAULT_MODEL, DEFAULT_CACHE_DIR)
    with psycopg.connect(url) as conn:
        data = build_snapshot(conn, DEFAULT_MODEL, vectors)
    out_path.write_bytes(data)
    return len(data)


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
    url = os.environ.get("DATABASE_URL")
    if not url:
        print(f"{SNAPSHOT_FILE}: skipped, DATABASE_URL is not set (use --env-file .env)")
        return 0
    size = build_snapshot_file(url, FIXTURE_DIR / SNAPSHOT_FILE)
    print(f"{SNAPSHOT_FILE}: {size / 1_000_000:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
