"""Rebuild tests/fixtures/*.html.gz from the raw filings cached in data/raw/.

The fixtures are the unmodified EDGAR HTML of six real filings (one 10-K and
one 10-Q per ticker), gzipped.  Run ``uv run python -m ingest.corpus`` first so
data/raw/ is populated.

    uv run python scripts/build_fixtures.py
"""
from __future__ import annotations

import gzip
import sys
from pathlib import Path

RAW_DIR = Path("data/raw")
FIXTURE_DIR = Path("tests/fixtures")

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


def main() -> int:
    missing = [a for a in FIXTURES.values() if not (RAW_DIR / f"{a}.html").is_file()]
    if missing:
        print(f"Missing from {RAW_DIR}/: {', '.join(missing)}", file=sys.stderr)
        return 1
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, accession in FIXTURES.items():
        build_fixture(RAW_DIR / f"{accession}.html", FIXTURE_DIR / f"{name}.html.gz")
        print(f"{name}: {accession}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
