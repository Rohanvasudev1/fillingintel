"""Tests for ingest/edgar_client.py — run before implementation (RED phase).

All tests use httpx.MockTransport so pytest-socket never sees external traffic.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from ingest.edgar_client import EdgarClient, derive_fiscal_period
from ingest.models import FilingMeta

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _mock_client(cache_dir: Path, responses: dict[str, bytes]) -> EdgarClient:
    """EdgarClient backed by an in-memory httpx.MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        content = responses.get(url)
        if content is None:
            raise AssertionError(f"Unexpected URL in test: {url}")
        return httpx.Response(200, content=content)

    return EdgarClient(
        cache_dir=cache_dir,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _filing(
    *,
    cik: str = "1045810",
    accession_no: str = "0001045810-25-000003",
    form_type: str = "10-K",
    fiscal_period: str = "FY2025",
    primary_document: str = "nvda-20250126.htm",
    report_date: date = date(2025, 1, 26),
    filing_date: date = date(2025, 2, 26),
) -> FilingMeta:
    return FilingMeta(
        cik=cik,
        accession_no=accession_no,
        form_type=form_type,
        company_name="NVIDIA CORP",
        fiscal_period=fiscal_period,
        report_date=report_date,
        filing_date=filing_date,
        primary_document=primary_document,
    )


def _archive_url(filing: FilingMeta) -> str:
    nodash = filing.accession_no.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(filing.cik)}"
        f"/{nodash}/{filing.primary_document}"
    )


SAMPLE_SUBMISSIONS: dict = {
    "name": "NVIDIA CORP",
    "fiscalYearEnd": "0126",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0001045810-25-000003",
                "0001045810-25-000001",  # 10-K/A — must be excluded
                "0001045810-24-000010",
            ],
            "form": ["10-K", "10-K/A", "10-Q"],
            "reportDate": ["2025-01-26", "2025-01-26", "2024-10-27"],
            "filingDate": ["2025-02-26", "2025-03-01", "2024-11-20"],
            "primaryDocument": [
                "nvda-20250126.htm",
                "nvda-20250126a.htm",
                "nvda-20241027.htm",
            ],
        }
    },
}


# ─── EDGAR_USER_AGENT guard ───────────────────────────────────────────────────


def test_missing_user_agent_raises(monkeypatch):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    with pytest.raises(EnvironmentError, match="EDGAR_USER_AGENT"):
        EdgarClient(cache_dir=Path("/tmp/irrelevant"))


def test_whitespace_only_user_agent_raises(monkeypatch):
    monkeypatch.setenv("EDGAR_USER_AGENT", "   ")
    with pytest.raises(EnvironmentError, match="EDGAR_USER_AGENT"):
        EdgarClient(cache_dir=Path("/tmp/irrelevant"))


# ─── Caching ─────────────────────────────────────────────────────────────────


def test_cache_miss_downloads_and_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    f = _filing()
    content = b"<html>10-K content</html>"
    edgar = _mock_client(tmp_path, {_archive_url(f): content})

    result = edgar.download_filing(f)

    assert result.exists()
    assert result.read_bytes() == content
    assert result.name == f"{f.accession_no}.html"


def test_cache_hit_skips_http(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    f = _filing()
    cache_dir = tmp_path / "raw"
    cache_dir.mkdir()
    cached = cache_dir / f"{f.accession_no}.html"
    cached.write_bytes(b"<html>cached</html>")

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"HTTP request made for cached filing: {request.url}")

    edgar = EdgarClient(
        cache_dir=cache_dir,
        http_client=httpx.Client(transport=httpx.MockTransport(fail)),
    )
    result = edgar.download_filing(f)
    assert result.read_bytes() == b"<html>cached</html>"


def test_cache_path_uses_accession_number(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    f = _filing(accession_no="0001045810-25-000003")
    edgar = _mock_client(tmp_path, {_archive_url(f): b"<html/>"})
    result = edgar.download_filing(f)
    assert result.name == "0001045810-25-000003.html"


# ─── Rate limiting ────────────────────────────────────────────────────────────


def test_rate_limit_sleep_between_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    f_a = _filing(accession_no="0001045810-25-000003", primary_document="a.htm")
    f_b = _filing(
        accession_no="0001045810-24-000010",
        form_type="10-Q",
        fiscal_period="FY2025-Q3",
        primary_document="b.htm",
        report_date=date(2024, 10, 27),
        filing_date=date(2024, 11, 20),
    )
    content = b"<html/>"
    edgar = _mock_client(
        tmp_path,
        {_archive_url(f_a): content, _archive_url(f_b): content},
    )

    sleep_calls: list[float] = []
    with patch("ingest.edgar_client.time.sleep", side_effect=sleep_calls.append):
        edgar.download_filing(f_a)
        edgar.download_filing(f_b)

    # The second request must have been preceded by a sleep of at least 100 ms
    # — EDGAR's limit is 10 req/s so any correct implementation must sleep ≥ 100 ms.
    assert len(sleep_calls) >= 1
    assert max(sleep_calls) >= 0.10  # at least 100 ms


# ─── Amended-filing exclusion ─────────────────────────────────────────────────


def test_amended_filings_excluded_from_list(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    url = "https://data.sec.gov/submissions/CIK0001045810.json"
    edgar = _mock_client(tmp_path, {url: json.dumps(SAMPLE_SUBMISSIONS).encode()})

    with caplog.at_level(logging.INFO, logger="ingest.edgar_client"):
        filings = edgar.list_filings("1045810")

    assert all(f.form_type not in {"10-K/A", "10-Q/A"} for f in filings)
    assert len(filings) == 2
    assert any("10-K/A" in r.message for r in caplog.records)


def test_amended_filings_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    url = "https://data.sec.gov/submissions/CIK0001045810.json"
    edgar = _mock_client(tmp_path, {url: json.dumps(SAMPLE_SUBMISSIONS).encode()})

    with caplog.at_level(logging.INFO, logger="ingest.edgar_client"):
        edgar.list_filings("1045810")

    assert any("0001045810-25-000001" in r.message for r in caplog.records)


# ─── list_filings metadata ────────────────────────────────────────────────────


def test_list_filings_sets_fiscal_period_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    url = "https://data.sec.gov/submissions/CIK0001045810.json"
    edgar = _mock_client(tmp_path, {url: json.dumps(SAMPLE_SUBMISSIONS).encode()})

    filings = edgar.list_filings("1045810")

    tenk = next(f for f in filings if f.form_type == "10-K")
    tenq = next(f for f in filings if f.form_type == "10-Q")

    # NVDA 10-K, reportDate 2025-01-26, FYE Jan 26 → FY2025
    assert tenk.fiscal_period == "FY2025"
    assert tenk.primary_document == "nvda-20250126.htm"
    assert tenk.company_name == "NVIDIA CORP"

    # NVDA 10-Q, reportDate 2024-10-27, FYE Jan 26 → FY2025-Q3
    assert tenq.fiscal_period == "FY2025-Q3"
    assert tenq.primary_document == "nvda-20241027.htm"


# ─── Non-target form skipping ────────────────────────────────────────────────


def test_non_target_form_skipped(tmp_path, monkeypatch):
    """Forms that are neither target (10-K/10-Q) nor amended are silently skipped."""
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    payload = {
        "name": "NVIDIA CORP",
        "fiscalYearEnd": "0126",
        "filings": {
            "recent": {
                "accessionNumber": ["0001045810-25-999001"],
                "form": ["8-K"],            # irrelevant form
                "reportDate": ["2025-01-26"],
                "filingDate": ["2025-01-27"],
                "primaryDocument": ["nvda-8k.htm"],
            }
        },
    }
    url = "https://data.sec.gov/submissions/CIK0001045810.json"
    edgar = _mock_client(tmp_path, {url: json.dumps(payload).encode()})

    filings = edgar.list_filings("1045810")

    assert filings == []


# ─── Malformed EDGAR response shape ──────────────────────────────────────────


def test_malformed_edgar_response_returns_empty(tmp_path, monkeypatch, caplog):
    """list_filings returns [] and warns when EDGAR fields are not lists."""
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    bad_payload = {
        "name": "BAD CORP",
        "fiscalYearEnd": "1231",
        "filings": {
            "recent": {
                "accessionNumber": "not-a-list",  # wrong type
                "form": ["10-K"],
                "reportDate": ["2024-12-28"],
                "filingDate": ["2025-01-15"],
                "primaryDocument": ["bad-20241228.htm"],
            }
        },
    }
    url = "https://data.sec.gov/submissions/CIK0000099999.json"
    edgar = _mock_client(tmp_path, {url: json.dumps(bad_payload).encode()})

    with caplog.at_level(logging.WARNING, logger="ingest.edgar_client"):
        filings = edgar.list_filings("99999")

    assert filings == []
    assert any("Unexpected EDGAR" in r.message for r in caplog.records)


def test_blank_report_date_skipped(tmp_path, monkeypatch, caplog):
    """Entries with blank reportDate are skipped with a warning."""
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    payload = {
        "name": "NVIDIA CORP",
        "fiscalYearEnd": "0126",
        "filings": {
            "recent": {
                "accessionNumber": ["0001045810-25-000003"],
                "form": ["10-K"],
                "reportDate": [""],   # blank
                "filingDate": ["2025-02-26"],
                "primaryDocument": ["nvda-20250126.htm"],
            }
        },
    }
    url = "https://data.sec.gov/submissions/CIK0001045810.json"
    edgar = _mock_client(tmp_path, {url: json.dumps(payload).encode()})

    with caplog.at_level(logging.WARNING, logger="ingest.edgar_client"):
        filings = edgar.list_filings("1045810")

    assert filings == []
    assert any("No report date" in r.message for r in caplog.records)


def test_blank_filing_date_skipped(tmp_path, monkeypatch, caplog):
    """Entries with blank filingDate are skipped with a warning."""
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    payload = {
        "name": "NVIDIA CORP",
        "fiscalYearEnd": "0126",
        "filings": {
            "recent": {
                "accessionNumber": ["0001045810-25-000003"],
                "form": ["10-K"],
                "reportDate": ["2025-01-26"],
                "filingDate": [""],   # blank
                "primaryDocument": ["nvda-20250126.htm"],
            }
        },
    }
    url = "https://data.sec.gov/submissions/CIK0001045810.json"
    edgar = _mock_client(tmp_path, {url: json.dumps(payload).encode()})

    with caplog.at_level(logging.WARNING, logger="ingest.edgar_client"):
        filings = edgar.list_filings("1045810")

    assert filings == []
    assert any("No filing date" in r.message for r in caplog.records)


# ─── HTTP error logging ───────────────────────────────────────────────────────


def test_http_error_logged_and_raised(tmp_path, monkeypatch, caplog):
    """A 4xx response is logged at ERROR with the URL, then HTTPStatusError is raised."""
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    f = _filing()

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    edgar = EdgarClient(
        cache_dir=tmp_path,
        http_client=httpx.Client(transport=httpx.MockTransport(error_handler)),
    )

    with caplog.at_level(logging.ERROR, logger="ingest.edgar_client"):
        with pytest.raises(httpx.HTTPStatusError):
            edgar.download_filing(f)

    assert any("404" in r.message for r in caplog.records)
    assert any(f.primary_document in r.message for r in caplog.records)


# ─── max_filings cutoff ───────────────────────────────────────────────────────


def test_max_filings_cutoff(tmp_path, monkeypatch):
    """list_filings stops at max_filings even if more are available."""
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    url = "https://data.sec.gov/submissions/CIK0001045810.json"
    edgar = _mock_client(tmp_path, {url: json.dumps(SAMPLE_SUBMISSIONS).encode()})

    filings = edgar.list_filings("1045810", max_filings=1)

    assert len(filings) == 1


# ─── close() and context manager ─────────────────────────────────────────────


def test_close_closes_http_client(tmp_path, monkeypatch):
    """close() delegates to the underlying httpx.Client."""
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    mock_http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    edgar = EdgarClient(cache_dir=tmp_path, http_client=mock_http)

    edgar.close()

    # After close(), the underlying client is closed; further requests raise.
    with pytest.raises(Exception):
        mock_http.get("http://example.com")


def test_context_manager_closes_on_exit(tmp_path, monkeypatch):
    """EdgarClient can be used as a context manager; client is closed on __exit__."""
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test/1.0 test@example.com")
    mock_http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    with EdgarClient(cache_dir=tmp_path, http_client=mock_http) as edgar:
        assert edgar is not None  # __enter__ returns self

    # Client should be closed after the with-block exits.
    with pytest.raises(Exception):
        mock_http.get("http://example.com")


# ─── derive_fiscal_period — edge cases ───────────────────────────────────────


def test_derive_fiscal_period_bad_mmdd_raises():
    """A malformed fiscal_year_end_mmdd raises ValueError."""
    with pytest.raises(ValueError, match="MMDD"):
        derive_fiscal_period("10-K", "2025-01-26", "126")   # 3 chars, not 4

    with pytest.raises(ValueError, match="MMDD"):
        derive_fiscal_period("10-K", "2025-01-26", "01AB")  # non-digit chars


def test_derive_fiscal_period_quarter_clamp_warns(caplog):
    """A report date very late in the fiscal year rounds to Q4, which is clamped to Q3.

    Synthetic date: FYE = Dec 28, report_date = Nov 15 of the same year.
    days_elapsed = 323, days_in_year = 366 (2024 leap year).
    323 * 4 / 366 ≈ 3.53 → round() → 4 → clamped to 3, warning emitted.
    """
    with caplog.at_level(logging.WARNING, logger="ingest.edgar_client"):
        result = derive_fiscal_period("10-Q", "2024-11-15", "1228")

    assert result == "FY2024-Q3"
    assert any("clamped" in r.message for r in caplog.records)


# ─── derive_fiscal_period ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "form_type, report_date, fye_mmdd, expected",
    [
        # AMD 10-K, calendar year ending Dec 28
        ("10-K", "2024-12-28", "1228", "FY2024"),
        # INTC 10-K, calendar year ending Dec 30
        ("10-K", "2023-12-30", "1230", "FY2023"),
        # NVDA 10-K, fiscal year ending late January
        ("10-K", "2025-01-26", "0126", "FY2025"),
        ("10-K", "2024-01-28", "0126", "FY2024"),
        # AMD 10-Q Q1, Q2, Q3 (calendar year)
        ("10-Q", "2024-03-30", "1228", "FY2024-Q1"),
        ("10-Q", "2024-06-29", "1228", "FY2024-Q2"),
        ("10-Q", "2024-09-28", "1228", "FY2024-Q3"),
        # NVDA 10-Q — all three quarters land in calendar year *before* FY end
        ("10-Q", "2024-04-28", "0126", "FY2025-Q1"),  # Apr 2024 → FY2025
        ("10-Q", "2024-07-28", "0126", "FY2025-Q2"),  # Jul 2024 → FY2025
        ("10-Q", "2024-10-27", "0126", "FY2025-Q3"),  # Oct 2024 → FY2025
    ],
)
def test_derive_fiscal_period(form_type, report_date, fye_mmdd, expected):
    assert derive_fiscal_period(form_type, report_date, fye_mmdd) == expected
