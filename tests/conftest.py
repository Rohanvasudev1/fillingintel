"""Shared fixtures for parser tests — loads gzipped HTML from tests/fixtures/."""
import gzip
import os
from datetime import date
from pathlib import Path

# tiktoken loads cl100k_base when ingest.chunker is imported, before pytest-socket
# blocks the network; point it at the vendored copy so tests never download it.
os.environ["TIKTOKEN_CACHE_DIR"] = str(Path(__file__).resolve().parents[1] / "vendor" / "tiktoken")

import pytest

from ingest.models import FilingMeta

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_html(name: str) -> str:
    path = FIXTURES_DIR / f"{name}.html.gz"
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ── raw HTML (session-scoped: loaded once) ────────────────────────────────────

@pytest.fixture(scope="session")
def nvda_10k_html() -> str:
    return _load_html("nvda_10k")


@pytest.fixture(scope="session")
def intc_10k_html() -> str:
    return _load_html("intc_10k")


@pytest.fixture(scope="session")
def nvda_10q_html() -> str:
    return _load_html("nvda_10q")


@pytest.fixture(scope="session")
def amd_10k_html() -> str:
    return _load_html("amd_10k")


@pytest.fixture(scope="session")
def amd_10q_html() -> str:
    return _load_html("amd_10q")


@pytest.fixture(scope="session")
def intc_10q_html() -> str:
    return _load_html("intc_10q")


# ── FilingMeta objects ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def nvda_10k_meta() -> FilingMeta:
    return FilingMeta(
        cik="1045810",
        accession_no="0001045810-26-000021",
        form_type="10-K",
        company_name="NVIDIA CORP",
        fiscal_period="FY2026",
        report_date=date(2026, 1, 26),
        filing_date=date(2026, 2, 26),
        primary_document="nvda-20260126.htm",
    )


@pytest.fixture(scope="session")
def intc_10k_meta() -> FilingMeta:
    return FilingMeta(
        cik="50863",
        accession_no="0000050863-26-000011",
        form_type="10-K",
        company_name="INTEL CORP",
        fiscal_period="FY2025",
        report_date=date(2025, 12, 27),
        filing_date=date(2026, 1, 31),
        primary_document="intc-20251227.htm",
    )


@pytest.fixture(scope="session")
def nvda_10q_meta() -> FilingMeta:
    return FilingMeta(
        cik="1045810",
        accession_no="0001045810-26-000075",
        form_type="10-Q",
        company_name="NVIDIA CORP",
        fiscal_period="FY2027-Q2",
        report_date=date(2026, 7, 27),
        filing_date=date(2026, 8, 28),
        primary_document="nvda-20260727.htm",
    )


@pytest.fixture(scope="session")
def amd_10k_meta() -> FilingMeta:
    return FilingMeta(
        cik="2488",
        accession_no="0000002488-26-000018",
        form_type="10-K",
        company_name="ADVANCED MICRO DEVICES INC",
        fiscal_period="FY2025",
        report_date=date(2025, 12, 27),
        filing_date=date(2026, 2, 25),
        primary_document="amd-20251227.htm",
    )


@pytest.fixture(scope="session")
def amd_10q_meta() -> FilingMeta:
    return FilingMeta(
        cik="2488",
        accession_no="0000002488-26-000123",
        form_type="10-Q",
        company_name="ADVANCED MICRO DEVICES INC",
        fiscal_period="FY2026-Q2",
        report_date=date(2026, 6, 27),
        filing_date=date(2026, 8, 7),
        primary_document="amd-20260627.htm",
    )


@pytest.fixture(scope="session")
def intc_10q_meta() -> FilingMeta:
    return FilingMeta(
        cik="50863",
        accession_no="0000050863-26-000157",
        form_type="10-Q",
        company_name="INTEL CORP",
        fiscal_period="FY2026-Q2",
        report_date=date(2026, 6, 27),
        filing_date=date(2026, 7, 25),
        primary_document="intc-20260627.htm",
    )


# ── ParsedFiling results (session-scoped: parsed once per fixture set) ─────────

@pytest.fixture(scope="session")
def nvda_10k_filing(nvda_10k_html, nvda_10k_meta):
    from ingest.parser import parse_filing
    return parse_filing(nvda_10k_html, nvda_10k_meta)


@pytest.fixture(scope="session")
def intc_10k_filing(intc_10k_html, intc_10k_meta):
    from ingest.parser import parse_filing
    return parse_filing(intc_10k_html, intc_10k_meta)


@pytest.fixture(scope="session")
def nvda_10q_filing(nvda_10q_html, nvda_10q_meta):
    from ingest.parser import parse_filing
    return parse_filing(nvda_10q_html, nvda_10q_meta)


@pytest.fixture(scope="session")
def amd_10k_filing(amd_10k_html, amd_10k_meta):
    from ingest.parser import parse_filing
    return parse_filing(amd_10k_html, amd_10k_meta)


@pytest.fixture(scope="session")
def amd_10q_filing(amd_10q_html, amd_10q_meta):
    from ingest.parser import parse_filing
    return parse_filing(amd_10q_html, amd_10q_meta)


@pytest.fixture(scope="session")
def intc_10q_filing(intc_10q_html, intc_10q_meta):
    from ingest.parser import parse_filing
    return parse_filing(intc_10q_html, intc_10q_meta)


# ── Postgres (Step 3c) ─────────────────────────────────────────────────────────

FIXTURE_NAMES = ["nvda_10k", "nvda_10q", "amd_10k", "amd_10q", "intc_10k", "intc_10q"]


@pytest.fixture(scope="session")
def fixture_records(request):
    """``ParsedRecord`` for each of the six fixture filings."""
    from ingest.parsed_files import ParsedRecord

    return [
        ParsedRecord(
            meta=request.getfixturevalue(f"{name}_meta"),
            filing=request.getfixturevalue(f"{name}_filing"),
            parser_commit="test",
        )
        for name in FIXTURE_NAMES
    ]


@pytest.fixture(scope="session")
def db_conn():
    """A connection to ``DATABASE_URL`` with a throwaway schema on its search path.

    The schema is created for the test session and dropped afterwards, so tests
    never touch development data.  Skips when ``DATABASE_URL`` is unset;
    ``test_store.py`` has a guard that fails in CI if it is.
    """
    import uuid

    import psycopg
    from psycopg import sql

    from ingest.store import apply_schema

    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        # public stays on the path: extensions such as pgvector (Step 5) live there.
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        apply_schema(conn)
        conn.commit()
        try:
            yield conn
        finally:
            conn.rollback()
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            conn.commit()
