"""Full-corpus parser run: choose the filing manifest, parse each filing, report.

Usage (needs ``EDGAR_USER_AGENT`` in the environment; downloads are cached)::

    uv run python -m ingest.corpus [report_path]

Manifest rule: per company, the two most recent complete fiscal years (a 10-K
plus the three 10-Qs with the same fiscal_period year): 24 filings for three
companies.  Amended forms are already excluded by ``EdgarClient.list_filings``.
"""
from __future__ import annotations

import argparse
import logging
import re
import subprocess
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

import httpx

from ingest.edgar_client import EdgarClient
from ingest.models import FilingMeta, ParsedFiling, ParsedTable
from ingest.parser import (
    ANCHOR_TOLERANCE,
    REQUIRED_SECTIONS_10K,
    REQUIRED_SECTIONS_10Q,
    parse_filing_with_methods,
)

logger = logging.getLogger(__name__)

CIKS: dict[str, str] = {"NVDA": "1045810", "AMD": "2488", "INTC": "50863"}
N_FISCAL_YEARS = 2
QUARTERS = (1, 2, 3)  # 10-Qs filed per fiscal year; the fourth quarter is in the 10-K
MIN_SECTION_CHARS = 2000
# A table counts as inserted when this share of its labelled rows appear in the text.
_ROW_MATCH_RATIO = 0.8
_MIN_LABEL_CHARS = 6
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_REPORT_PATH = REPO_ROOT / "spikes" / "corpus_report.txt"
_GIT_TIMEOUT_SECONDS = 10


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SectionRow:
    """One parsed section: how it was located and how long it is."""

    key: str
    method: str
    length: int  # total over all spans of the key
    required: bool
    spans: int = 1

    @property
    def short(self) -> bool:
        return self.length < MIN_SECTION_CHARS


@dataclass(frozen=True)
class TableCounts:
    """Tables detected in a filing, split by whether they reached the section text."""

    detected: int
    inserted: int
    not_inserted: int
    indeterminate: int


@dataclass(frozen=True)
class FilingReport:
    """Everything the corpus report prints about one filing."""

    meta: FilingMeta
    sections: tuple[SectionRow, ...]
    missing_required: tuple[str, ...]
    tables: TableCounts


# ── Manifest ──────────────────────────────────────────────────────────────────

def _quarters_of(tenk: FilingMeta, company_filings: list[FilingMeta]) -> list[FilingMeta]:
    """The 10-Qs of *tenk*'s fiscal year (Q1-Q3), one per period; gaps are logged."""
    found: list[FilingMeta] = []
    for quarter in QUARTERS:
        period = f"{tenk.fiscal_period}-Q{quarter}"
        matches = sorted(
            (f for f in company_filings if f.form_type == "10-Q" and f.fiscal_period == period),
            key=lambda f: f.filing_date,
        )
        if not matches:
            logger.warning("CIK %s: no 10-Q found for %s", tenk.cik, period)
            continue
        if len(matches) > 1:
            logger.warning(
                "CIK %s: %d filings for %s; using the earliest", tenk.cik, len(matches), period
            )
        found.append(matches[0])
    return found


def select_manifest(
    filings: list[FilingMeta], n_years: int = N_FISCAL_YEARS
) -> list[FilingMeta]:
    """The *n_years* most recent complete fiscal years per company.

    A fiscal year is complete once its 10-K is filed.  For each, the 10-K and
    the three 10-Qs whose ``fiscal_period`` is ``<FY>-Q1`` to ``-Q3``.  A missing
    quarter is logged and skipped, so the result can be short.
    """
    picked: list[FilingMeta] = []
    for cik in sorted({f.cik for f in filings}):
        mine = [f for f in filings if f.cik == cik]
        tenks = sorted(
            (f for f in mine if f.form_type == "10-K"),
            key=lambda f: f.report_date,
            reverse=True,
        )[:n_years]
        for tenk in tenks:
            picked.append(tenk)
            picked.extend(_quarters_of(tenk, mine))
    return picked


# ── Table accounting ──────────────────────────────────────────────────────────

def _row_labels(markdown: str) -> list[str]:
    """First non-empty cell of each table row, keeping only text-like labels."""
    labels: list[str] = []
    for line in markdown.splitlines():
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        first = next((c for c in cells if c), "")
        if len(first) >= _MIN_LABEL_CHARS and any(ch.isalpha() for ch in first):
            labels.append(first.replace("\\|", "|"))
    return labels


def classify_tables(tables: list[ParsedTable], text: str) -> TableCounts:
    """Count detected tables that appear in *text* (the concatenated sections).

    ``ParsedTable.markdown`` is not byte-identical to the table as rendered
    inside the section text, so presence is judged by row labels.  Tables with
    no text-like row label (purely numeric) are reported as indeterminate.
    """
    inserted = not_inserted = indeterminate = 0
    for tbl in tables:
        labels = _row_labels(tbl.markdown)
        if not labels:
            indeterminate += 1
            continue
        found = sum(1 for lbl in labels if lbl in text)
        if found / len(labels) >= _ROW_MATCH_RATIO:
            inserted += 1
        else:
            not_inserted += 1
    return TableCounts(
        detected=len(tables),
        inserted=inserted,
        not_inserted=not_inserted,
        indeterminate=indeterminate,
    )


# ── Per-filing report ─────────────────────────────────────────────────────────

def _section_rows(
    filing: ParsedFiling, methods: dict[str, str], required: frozenset[str]
) -> tuple[SectionRow, ...]:
    """One row per label, in document order; a label with several spans sums them."""
    lengths: dict[str, int] = {}
    spans: dict[str, int] = {}
    for sec in sorted(filing.sections, key=lambda s: s.char_start):
        lengths[sec.label] = lengths.get(sec.label, 0) + sec.char_end - sec.char_start
        spans[sec.label] = spans.get(sec.label, 0) + 1
    return tuple(
        SectionRow(
            key=label,
            method=methods[label],
            length=length,
            required=label in required,
            spans=spans[label],
        )
        for label, length in lengths.items()
    )


def build_report(html: str, meta: FilingMeta) -> FilingReport:
    """Parse one filing and summarise sections, methods, lengths and tables."""
    filing, methods = parse_filing_with_methods(html, meta)
    required = REQUIRED_SECTIONS_10K if "10-K" in meta.form_type else REQUIRED_SECTIONS_10Q
    rows = _section_rows(filing, methods, required)
    return FilingReport(
        meta=meta,
        sections=rows,
        missing_required=tuple(filing.missing_sections),
        tables=classify_tables(filing.tables, filing.text),
    )


def _fmt_required(rep: FilingReport) -> str:
    """Render a filing's required sections as ``method / chars`` table cells."""
    order = REQUIRED_SECTIONS_10K if "10-K" in rep.meta.form_type else REQUIRED_SECTIONS_10Q
    by_key = {r.key: r for r in rep.sections}
    cells: list[str] = []
    for key in sorted(order):
        r = by_key.get(key)
        if r is None:
            cells.append("MISSING")
            continue
        spans = f" ({r.spans} spans)" if r.spans > 1 else ""
        cells.append(f"{r.method} / {r.length:,}{spans}{' ⚠' if r.short else ''}")
    return " | ".join(cells)


def format_report(reports: list[FilingReport], header: tuple[str, ...] = ()) -> str:
    """Render the per-filing tables as plain markdown.

    *header* lines (commit, versions, manifest rule) go under the title so a
    saved report says which code and config produced its numbers.
    """
    out: list[str] = ["# Full-corpus parser run", "", *header, ""]
    for form, order in (("10-K", REQUIRED_SECTIONS_10K), ("10-Q", REQUIRED_SECTIONS_10Q)):
        subset = [r for r in reports if r.meta.form_type == form]
        keys = sorted(order)
        out += [
            f"## {form}: required sections (method / chars; ⚠ = under {MIN_SECTION_CHARS:,})",
            "",
            "| Accession | Period | Present | " + " | ".join(keys) + " |",
            "|---|---|---|" + "---|" * len(keys),
        ]
        for r in subset:
            present = "yes" if not r.missing_required else "NO"
            out.append(
                f"| {r.meta.accession_no} | {r.meta.fiscal_period} | {present} | "
                f"{_fmt_required(r)} |"
            )
        out.append("")
    out += [
        f"## Sections under {MIN_SECTION_CHARS:,} characters (any section)",
        "",
        "| Accession | Form | Section | Method | Chars | Required |",
        "|---|---|---|---|---|---|",
    ]
    for r in reports:
        for s in r.sections:
            if s.short:
                out.append(
                    f"| {r.meta.accession_no} | {r.meta.form_type} | {s.key} | {s.method} "
                    f"| {s.length:,} | {'yes' if s.required else 'no'} |"
                )
    out += [
        "",
        "## Tables: detected vs present in section text",
        "",
        "| Accession | Form | Detected | Inserted | Not inserted | Indeterminate |",
        "|---|---|---|---|---|---|",
    ]
    for r in reports:
        t = r.tables
        out.append(
            f"| {r.meta.accession_no} | {r.meta.form_type} | {t.detected} | {t.inserted} "
            f"| {t.not_inserted} | {t.indeterminate} |"
        )
    return "\n".join(out) + "\n"


def method_counts(reports: list[FilingReport]) -> dict[str, int]:
    """Count extraction methods over *required* sections only."""
    counts: dict[str, int] = {}
    for r in reports:
        for s in r.sections:
            if s.required:
                counts[s.method] = counts.get(s.method, 0) + 1
    return counts


def _git_state() -> str:
    """Return ``<short commit>`` plus ``+dirty`` when the working tree has local changes."""
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
            timeout=_GIT_TIMEOUT_SECONDS,
        ).stdout.strip()

    try:
        commit = git("rev-parse", "--short", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{commit}{'+dirty' if dirty else ''}"


def run_header(n_filings: int) -> tuple[str, ...]:
    """Lines identifying the code, library version and rules behind a report."""
    return (
        f"- commit: {_git_state()}",
        f"- edgartools: {version('edgartools')}",
        f"- manifest: the {N_FISCAL_YEARS} most recent complete fiscal years per company, "
        f"each a 10-K plus its three 10-Qs aligned by fiscal_period ({n_filings} filings)",
        f"- thresholds: short section < {MIN_SECTION_CHARS:,} chars; "
        f"anchor tolerance {ANCHOR_TOLERANCE} chars",
        "- table counts: a table is 'inserted' when >=80% of its row labels occur "
        "anywhere in the filing's section text (an upper bound; shared labels such as "
        "'Net income' can over-count)",
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

class _Downloader(Protocol):
    def download_filing(self, filing: FilingMeta) -> Path: ...


def build_reports(
    client: _Downloader, manifest: list[FilingMeta]
) -> tuple[list[FilingReport], list[str]]:
    """Download and parse each filing; one bad filing does not stop the run.

    Returns the reports and a ``"<accession>: <error>"`` line per failure.
    """
    reports: list[FilingReport] = []
    failures: list[str] = []
    for meta in manifest:
        try:
            path = client.download_filing(meta)
            html = path.read_text(encoding="utf-8")
            reports.append(build_report(html, meta))
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.error("%s failed: %s", meta.accession_no, exc)
            failures.append(f"{meta.accession_no}: {type(exc).__name__}: {exc}")
    return reports, failures


def main(argv: list[str] | None = None) -> int:
    """Run the manifest, write the report, exit non-zero on any failure or missing section."""
    parser = argparse.ArgumentParser(description="Parse the filing manifest and report sections.")
    parser.add_argument("report_path", nargs="?", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    manifest: list[FilingMeta] = []
    with EdgarClient(DEFAULT_RAW_DIR) as client:
        for ticker, cik in CIKS.items():
            picked = select_manifest(client.list_filings(cik))
            logger.info("%s: %d filings selected", ticker, len(picked))
            manifest.extend(picked)
        reports, failures = build_reports(client, manifest)

    text = format_report(reports, run_header(len(manifest)))
    if failures:
        text += "\n## Failures\n\n" + "\n".join(f"- {line}" for line in failures) + "\n"
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(text, encoding="utf-8")
    print(text)
    print("Required-section extraction methods:", method_counts(reports))
    return 1 if failures or any(r.missing_required for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
