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
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

import httpx

from ingest.chunker import MAX_CHUNK_TOKENS, TOKENIZER, chunk_filing
from ingest.edgar_client import EdgarClient
from ingest.models import FilingMeta, ParsedFiling
from ingest.parsed_files import DEFAULT_PARSED_DIR, ParsedRecord, write_parsed
from ingest.parser import (
    ANCHOR_TOLERANCE,
    MIN_SECTION_CHARS,
    REQUIRED_SECTIONS_10K,
    REQUIRED_SECTIONS_10Q,
    parse_filing_with_methods,
)
from ingest.provenance import REPO_ROOT, git_state
from ingest.tables import (
    MAX_ROW_LINES,
    TableReconciliation,
    find_table_spans,
    reconcile_tables,
    tables_crossing_sections,
)

logger = logging.getLogger(__name__)

CIKS: dict[str, str] = {"NVDA": "1045810", "AMD": "2488", "INTC": "50863"}
TICKER_BY_CIK: dict[str, str] = {cik: ticker for ticker, cik in CIKS.items()}
N_FISCAL_YEARS = 2
QUARTERS = (1, 2, 3)  # 10-Qs filed per fiscal year; the fourth quarter is in the 10-K
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_REPORT_PATH = REPO_ROOT / "spikes" / "corpus_report.txt"


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
    """Table spans in the parsed text, reconciled with edgartools' table list."""

    reconciliation: TableReconciliation
    unclosed_rows: int
    glued_headers: int
    crossing_tables: int  # tables cut by a section boundary; must be 0


@dataclass(frozen=True)
class ChunkCounts:
    """Chunks the chunker makes from one filing."""

    total: int
    with_tables: int
    oversized: int  # over MAX_CHUNK_TOKENS; all must contain a table
    max_tokens: int


@dataclass(frozen=True)
class FilingReport:
    """Everything the corpus report prints about one filing."""

    meta: FilingMeta
    sections: tuple[SectionRow, ...]
    missing_required: tuple[str, ...]
    tables: TableCounts
    chunks: ChunkCounts


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

def count_tables(filing: ParsedFiling) -> TableCounts:
    """Table spans in *filing* and how they line up with edgartools' table list."""
    spans = find_table_spans(filing)
    return TableCounts(
        reconciliation=reconcile_tables(filing),
        unclosed_rows=sum(s.unclosed_rows for s in spans),
        glued_headers=sum(s.header_glued for s in spans),
        crossing_tables=len(tables_crossing_sections(filing)),
    )


def count_chunks(filing: ParsedFiling) -> ChunkCounts:
    """Summarise the chunks of *filing*."""
    chunks = chunk_filing(filing)
    return ChunkCounts(
        total=len(chunks),
        with_tables=sum(c.contains_table for c in chunks),
        oversized=sum(c.token_count > MAX_CHUNK_TOKENS for c in chunks),
        max_tokens=max((c.token_count for c in chunks), default=0),
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


def _report_for(meta: FilingMeta, filing: ParsedFiling, methods: dict[str, str]) -> FilingReport:
    required = REQUIRED_SECTIONS_10K if "10-K" in meta.form_type else REQUIRED_SECTIONS_10Q
    return FilingReport(
        meta=meta,
        sections=_section_rows(filing, methods, required),
        missing_required=tuple(filing.missing_sections),
        tables=count_tables(filing),
        chunks=count_chunks(filing),
    )


def build_report(html: str, meta: FilingMeta) -> FilingReport:
    """Parse one filing and summarise sections, methods, lengths, tables and chunks."""
    filing, methods = parse_filing_with_methods(html, meta)
    return _report_for(meta, filing, methods)


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
    """Render the per-filing tables as markdown; *header* lines go under the title."""
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
    out += ["", *_table_lines(reports), "", *_chunk_lines(reports)]
    return "\n".join(out) + "\n"


def _table_lines(reports: list[FilingReport]) -> list[str]:
    out = [
        "## Tables: edgartools tables reconciled with table spans in the parsed text",
        "",
        "| Accession | Form | edgartools tables | Rendered empty | Sharing a span "
        "| Spans without a table | Table spans | Not in text | Outside spans "
        "| Unclosed rows | Glued headers | Crossing a section |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        t, c = r.tables, r.tables.reconciliation
        out.append(
            f"| {r.meta.accession_no} | {r.meta.form_type} | {c.edgartools_tables} "
            f"| {c.rendered_empty} | {c.sharing_a_span} | {c.spans_without_table} "
            f"| {c.span_count} | {c.not_in_text} | {c.outside_spans} "
            f"| {t.unclosed_rows} | {t.glued_headers} | {t.crossing_tables} |"
        )
    return out


def _chunk_lines(reports: list[FilingReport]) -> list[str]:
    out = [
        f"## Chunks ({TOKENIZER} tokens; limit {MAX_CHUNK_TOKENS}; "
        "only table chunks may exceed it)",
        "",
        "| Accession | Form | Chunks | With tables "
        f"| Over {MAX_CHUNK_TOKENS} tokens | Max tokens |",
        "|---|---|---|---|---|---|",
    ]
    for r in reports:
        c = r.chunks
        out.append(
            f"| {r.meta.accession_no} | {r.meta.form_type} | {c.total} | {c.with_tables} "
            f"| {c.oversized} | {c.max_tokens} |"
        )
    return out


def method_counts(reports: list[FilingReport]) -> dict[str, int]:
    """Count extraction methods over *required* sections only."""
    counts: dict[str, int] = {}
    for r in reports:
        for s in r.sections:
            if s.required:
                counts[s.method] = counts.get(s.method, 0) + 1
    return counts


def run_header(n_filings: int) -> tuple[str, ...]:
    """Lines identifying the code, library version and rules behind a report."""
    return (
        f"- commit: {git_state()}",
        f"- edgartools: {version('edgartools')}",
        f"- manifest: the {N_FISCAL_YEARS} most recent complete fiscal years per company, "
        f"each a 10-K plus its three 10-Qs aligned by fiscal_period ({n_filings} filings)",
        f"- thresholds: short section < {MIN_SECTION_CHARS:,} chars; "
        f"anchor tolerance {ANCHOR_TOLERANCE} chars",
        "- table spans: runs of '|' rows in the parsed text, found per section "
        f"(a row may span up to {MAX_ROW_LINES} lines); 'crossing' counts tables a section "
        "boundary cuts and must be 0",
        "- reconciliation: table spans = edgartools tables - rendered empty - sharing a span "
        "- not in text - outside spans + spans without a table; 'not in text' and "
        "'outside spans' must be 0",
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

class _Downloader(Protocol):
    def download_filing(self, filing: FilingMeta) -> Path: ...


def build_reports(
    client: _Downloader, manifest: list[FilingMeta], parsed_dir: Path | None = None
) -> tuple[list[FilingReport], list[str]]:
    """Download and parse each filing; one bad filing does not stop the run.

    With *parsed_dir*, each parsed filing is also written there as
    ``{accession_no}.json`` for ``python -m ingest.load``.  Returns the reports
    and a ``"<accession>: <error>"`` line per failure.
    """
    commit = git_state()
    reports: list[FilingReport] = []
    failures: list[str] = []
    for meta in manifest:
        try:
            html = client.download_filing(meta).read_text(encoding="utf-8")
            filing, methods = parse_filing_with_methods(html, meta)
            reports.append(_report_for(meta, filing, methods))
            if parsed_dir is not None:
                record = ParsedRecord(meta=meta, filing=filing, parser_commit=commit)
                write_parsed(record, parsed_dir)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.error("%s failed: %s", meta.accession_no, exc)
            failures.append(f"{meta.accession_no}: {type(exc).__name__}: {exc}")
    return reports, failures


def main(argv: list[str] | None = None) -> int:
    """Run the manifest, write the report, exit non-zero on any failure or missing section."""
    parser = argparse.ArgumentParser(description="Parse the filing manifest and report sections.")
    parser.add_argument("report_path", nargs="?", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR,
        help="where to write data/parsed/{accession_no}.json for python -m ingest.load",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    manifest: list[FilingMeta] = []
    with EdgarClient(DEFAULT_RAW_DIR) as client:
        for ticker, cik in CIKS.items():
            picked = select_manifest(client.list_filings(cik))
            logger.info("%s: %d filings selected", ticker, len(picked))
            manifest.extend(picked)
        reports, failures = build_reports(client, manifest, args.parsed_dir)

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
