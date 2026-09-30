"""EDGAR HTTP client: enumerate filings, download raw HTML, enforce rate limits.

Design decisions:
- User-Agent is read from ``EDGAR_USER_AGENT`` env var at construction time.
  Missing or blank → OSError with a clear message.
- Raw HTML is cached to ``{cache_dir}/{accession_no}.html``.  A cached file is
  never re-downloaded, so the ingest script is safe to re-run.
- Requests are rate-limited to one every ``RATE_LIMIT_SLEEP`` seconds (≤ 10/s).
- Amended filings (10-K/A, 10-Q/A, …) are excluded and logged.
- fiscal_period is derived from EDGAR fiscal metadata, not the filing date,
  so NVDA's late-January fiscal year is handled correctly.

Inject an ``httpx.Client`` in tests to avoid any real network traffic.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ingest.models import FilingMeta

logger = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = (
    "https://www.sec.gov/Archives/edgar/data"
    "/{cik_int}/{accession_nodash}/{document}"
)
RATE_LIMIT_SLEEP: float = 0.11  # stay well under EDGAR's 10 req/s limit
AMENDED_FORMS: frozenset[str] = frozenset({"10-K/A", "10-Q/A", "10-KT/A", "10-QT/A"})
TARGET_FORMS: frozenset[str] = frozenset({"10-K", "10-Q"})


def _get_user_agent() -> str:
    ua = os.environ.get("EDGAR_USER_AGENT", "").strip()
    if not ua:
        raise OSError(
            "EDGAR_USER_AGENT environment variable is required. "
            "Set it to identify your application and provide a contact address, e.g.: "
            "'FilingIntel/1.0 contact@example.com'"
        )
    return ua


def derive_fiscal_period(
    form_type: str,
    report_date_str: str,
    fiscal_year_end_mmdd: str,
) -> str:
    """Return the fiscal period string for a filing.

    Returns ``"FY2025"`` for a 10-K and ``"FY2025-Q3"`` for a 10-Q.

    Args:
        form_type: ``"10-K"`` or ``"10-Q"``.
        report_date_str: ISO date from EDGAR, e.g. ``"2025-01-26"``.
        fiscal_year_end_mmdd: 4-char MMDD from the EDGAR company record,
            e.g. ``"0126"`` for NVDA (January 26).
    """
    if len(fiscal_year_end_mmdd) != 4 or not fiscal_year_end_mmdd.isdigit():
        raise ValueError(
            f"fiscal_year_end_mmdd must be a 4-digit MMDD string, got {fiscal_year_end_mmdd!r}"
        )
    report_date = date.fromisoformat(report_date_str)
    fye_month = int(fiscal_year_end_mmdd[:2])
    fye_day = int(fiscal_year_end_mmdd[2:])

    # Find the fiscal-year-end on or after the report date.
    candidate = date(report_date.year, fye_month, fye_day)
    if candidate < report_date:
        candidate = date(report_date.year + 1, fye_month, fye_day)

    fiscal_year = candidate.year

    if "10-K" in form_type:
        # For annual filings the fiscal year number is simply the year of the
        # report date.  Using report_date.year directly avoids the edge case
        # where the actual filing date is a day or two after the nominal FYE
        # (e.g. NVDA FY2024 ended 2024-01-28 but fye_mmdd = "0126").
        return f"FY{report_date.year}"

    # 10-Q: determine quarter (1–3).
    # round() handles the case where the report date falls exactly at a
    # quarter boundary — e.g. NVDA Q3 at 75.1 % of a leap-year fiscal year
    # rounds correctly to Q3.
    prev_fye = date(candidate.year - 1, fye_month, fye_day)
    days_elapsed = (report_date - prev_fye).days
    days_in_year = (candidate - prev_fye).days
    raw_quarter = round(days_elapsed * 4 / days_in_year)
    quarter = max(1, min(3, raw_quarter))
    if raw_quarter != quarter:
        logger.warning(
            "Quarter %d clamped to %d for report_date=%s fye=%s",
            raw_quarter,
            quarter,
            report_date_str,
            fiscal_year_end_mmdd,
        )
    return f"FY{fiscal_year}-Q{quarter}"


class EdgarClient:
    """Rate-limited EDGAR HTTP client with on-disk caching.

    Parameters
    ----------
    cache_dir:
        Directory for cached raw HTML files.  Created if it does not exist.
    http_client:
        Optional pre-built ``httpx.Client``.  Pass a client backed by
        ``httpx.MockTransport`` in tests so no real network traffic occurs.
    """

    def __init__(
        self,
        cache_dir: Path,
        http_client: httpx.Client | None = None,
    ) -> None:
        user_agent = _get_user_agent()
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = http_client or httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=30.0,
            follow_redirects=True,
        )
        self._last_request_time: float = 0.0

    # ── Internal helpers ────────────────────────────────────────────────────

    def _get(self, url: str) -> httpx.Response:
        """Rate-limited GET — sleeps to stay under EDGAR's 10 req/s limit."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < RATE_LIMIT_SLEEP:
            time.sleep(RATE_LIMIT_SLEEP - elapsed)
        response = self._http.get(url)
        self._last_request_time = time.monotonic()
        if response.is_error:
            logger.error("HTTP %d for %s", response.status_code, url)
        response.raise_for_status()
        return response

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ── Public API ──────────────────────────────────────────────────────────

    def list_filings(
        self,
        cik: str,
        form_types: frozenset[str] | set[str] | None = None,
        max_filings: int = 50,
    ) -> list[FilingMeta]:
        """Return filings from the EDGAR submissions API.

        Amended forms (10-K/A, 10-Q/A, …) are excluded and logged at INFO.
        Results are in EDGAR order (most-recent first).
        """
        if form_types is None:
            form_types = TARGET_FORMS

        padded_cik = cik.zfill(10)
        data: dict[str, Any] = self._get(SUBMISSIONS_URL.format(cik=padded_cik)).json()

        company_name: str = data.get("name", "") or ""
        fye_mmdd: str = data.get("fiscalYearEnd", "1231") or "1231"

        recent: dict[str, Any] = data.get("filings", {}).get("recent", {})
        accessions: list[str] = recent.get("accessionNumber", [])
        forms: list[str] = recent.get("form", [])
        report_dates: list[str] = recent.get("reportDate", [])
        filing_dates: list[str] = recent.get("filingDate", [])
        primary_docs: list[str] = recent.get("primaryDocument", [])

        if not all(
            isinstance(lst, list)
            for lst in (accessions, forms, report_dates, filing_dates, primary_docs)
        ):
            logger.warning("Unexpected EDGAR response shape for CIK %s; skipping", cik)
            return []

        results: list[FilingMeta] = []
        for accession, form, report_date, filing_date, primary_doc in zip(
            accessions, forms, report_dates, filing_dates, primary_docs
        ):
            if len(results) >= max_filings:
                break

            if form in AMENDED_FORMS:
                logger.info(
                    "Skipping amended filing %s (%s)", accession, form
                )
                continue

            if form not in form_types:
                continue

            if not report_date:
                logger.warning("No report date for %s; skipping", accession)
                continue

            if not filing_date:
                logger.warning("No filing date for %s; skipping", accession)
                continue

            fiscal_period = derive_fiscal_period(form, report_date, fye_mmdd)
            results.append(
                FilingMeta(
                    cik=cik,
                    accession_no=accession,
                    form_type=form,
                    company_name=company_name,
                    fiscal_period=fiscal_period,
                    report_date=date.fromisoformat(report_date),
                    filing_date=date.fromisoformat(filing_date),
                    primary_document=primary_doc,
                )
            )

        return results

    def download_filing(self, filing: FilingMeta) -> Path:
        """Download raw HTML for *filing* to ``{cache_dir}/{accession_no}.html``.

        Returns the path.  Does not re-download if the file already exists.
        """
        cache_path = self._cache_dir / f"{filing.accession_no}.html"
        if cache_path.exists():
            logger.debug("Cache hit: %s", filing.accession_no)
            return cache_path

        accession_nodash = filing.accession_no.replace("-", "")
        url = ARCHIVE_URL.format(
            cik_int=int(filing.cik),
            accession_nodash=accession_nodash,
            document=filing.primary_document,
        )
        response = self._get(url)
        cache_path.write_bytes(response.content)
        logger.info("Downloaded %s → %s", filing.accession_no, cache_path.name)
        return cache_path
