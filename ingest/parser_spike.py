"""Parser spike: compare edgartools vs sec-parser on 6 EDGAR filings.

Run with:
    uv run --group spike python -m ingest.parser_spike

Writes results to spikes/parser_spike_report.txt.

Checks per filing × parser:
  1. TOC false positive  — did the parser mistake a table-of-contents entry
                           for a real section start?
  2. Table survival      — are HTML tables preserved in a detectable form?
  3. Char-offset accuracy — does text[char_start:char_end] round-trip to the
                            right passage? (edgartools only; sec-parser gives
                            an element tree, not char offsets)

Additional cross-check (per filing):
  4. dei fiscal-period   — compare derive_fiscal_period() against
                           dei:DocumentFiscalYearFocus + dei:DocumentFiscalPeriodFocus
                           extracted from inline XBRL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env so EDGAR_USER_AGENT is available when running the spike locally.
load_dotenv()

from ingest.edgar_client import EdgarClient  # noqa: E402

# ── Spike targets ─────────────────────────────────────────────────────────────
# One 10-K + one 10-Q per ticker.  CIKs are zero-padded to 10 digits.
TARGETS: list[tuple[str, str]] = [
    ("1045810", "NVDA"),   # NVIDIA
    ("2488",    "AMD"),    # Advanced Micro Devices
    ("50863",   "INTC"),   # Intel
]

CACHE_DIR = Path("data/raw")
REPORT_PATH = Path("spikes/parser_spike_report.txt")

# Known 10-K section labels for acceptance check (form-type-aware).
# Each entry is a tuple of acceptable aliases (edgartools snake_case OR
# sec-parser verbose text) — any match satisfies the requirement.
REQUIRED_10K_SECTIONS: list[tuple[str, ...]] = [
    ("part_i_item_1a", "item 1a", "item1a"),       # Risk Factors
    ("part_ii_item_7", "item 7", "item7"),          # MD&A
    ("part_ii_item_8", "item 8", "item8"),          # Financial Statements
]
REQUIRED_10Q_SECTIONS: list[tuple[str, ...]] = [
    ("part_i_item_1", "part i item 1", "part i. item 1"),    # Financial Statements
    ("part_i_item_2", "part i item 2", "part i. item 2"),    # MD&A
    ("part_ii_item_1a", "part ii item 1a", "part ii. item 1a"),  # Legal Proceedings / Risk
]

# Regex that matches a table-of-contents heading line (no body text, just a
# label followed by a page number or ellipsis).
_TOC_RE = re.compile(
    r"^\s*(part\s+[ivx]+|item\s+\d+[a-z]?)[.\s]*\.{3,}[\s\d]+$",
    re.IGNORECASE,
)


# ── Result containers ─────────────────────────────────────────────────────────

@dataclass
class FilingResult:
    ticker: str
    accession_no: str
    form_type: str
    fiscal_period_derived: str
    fiscal_period_xbrl: str          # from dei tags; "" if unavailable
    fiscal_period_match: bool | None  # None = XBRL unavailable

    # edgartools results
    et_sections_found: list[str] = field(default_factory=list)
    et_toc_false_positives: list[str] = field(default_factory=list)
    et_tables_detected: int = 0
    et_offset_samples: list[tuple[str, bool, str]] = field(default_factory=list)
    # (label, round_trips_ok, snippet)
    et_error: str = ""

    # sec-parser results
    sp_sections_found: list[str] = field(default_factory=list)
    sp_toc_false_positives: list[str] = field(default_factory=list)
    sp_tables_detected: int = 0
    sp_error: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_dei_fiscal_period(html: str, form_type: str) -> str:
    """Return a fiscal-period string derived from inline XBRL dei tags.

    Looks for dei:DocumentFiscalYearFocus and dei:DocumentFiscalPeriodFocus.
    Returns "" if the tags are absent (e.g. older plain-HTML filings).
    """
    try:
        from edgar.documents.document import Document

        doc = Document.from_html(html) if hasattr(Document, "from_html") else None
        if doc is None:
            # Fallback: use parse_html
            from edgar.documents import parse_html
            doc = parse_html(html)

        facts = doc.xbrl_facts
        year_val = ""
        period_val = ""
        for fact in facts:
            if fact.concept == "dei:DocumentFiscalYearFocus":
                year_val = (fact.value or "").strip()
            elif fact.concept == "dei:DocumentFiscalPeriodFocus":
                period_val = (fact.value or "").strip()

        if not year_val:
            return ""

        if "10-K" in form_type or period_val.upper() in ("FY", "ANNUAL", ""):
            return f"FY{year_val}"

        quarter_map = {"Q1": "Q1", "Q2": "Q2", "Q3": "Q3"}
        q = quarter_map.get(period_val.upper(), period_val)
        return f"FY{year_val}-{q}" if q else f"FY{year_val}"

    except Exception as exc:  # noqa: BLE001
        return f"ERROR:{exc}"


def _is_toc_line(text: str) -> bool:
    return bool(_TOC_RE.match(text.strip()))


# ── edgartools probe ──────────────────────────────────────────────────────────

def _probe_edgartools(
    html: str, form_type: str, result: FilingResult
) -> None:
    try:
        from edgar.documents import parse_html

        doc = parse_html(html)

        # Section detection
        available = doc.get_available_sec_sections()
        result.et_sections_found = list(available)

        # TOC false-positive check: a "section" whose full text is just a
        # short heading + page number is almost certainly a TOC entry.
        for sec_name in available:
            text = doc.get_sec_section(sec_name, clean=True) or ""
            # Short sections (< 200 chars) that look like TOC lines are FPs.
            first_line = text.splitlines()[0] if text.strip() else ""
            if len(text.strip()) < 200 and _is_toc_line(first_line):
                result.et_toc_false_positives.append(sec_name)

        # Table detection
        result.et_tables_detected = len(doc.tables) if hasattr(doc, "tables") else 0

        # Char-offset round-trip.
        # doc.text is a method, not a property — call it.
        # edgartools normalises whitespace differently from get_sec_section(),
        # so we test with whitespace-normalised content.
        full_text: str = doc.text() if callable(doc.text) else (doc.text or "")
        full_norm = re.sub(r"\s+", " ", full_text).strip()
        for sec_name in available[:5]:  # sample up to 5 sections
            sec_text = doc.get_sec_section(sec_name, clean=False) or ""
            if not sec_text:
                continue
            sec_norm = re.sub(r"\s+", " ", sec_text).strip()
            if not sec_norm:
                continue
            probe = sec_norm[:60]
            idx = full_norm.find(probe)
            if idx == -1:
                result.et_offset_samples.append(
                    (sec_name, False, "Not found in full text (whitespace-normalised)")
                )
            else:
                # Check the full normalised section content round-trips
                sliced = full_norm[idx: idx + len(sec_norm)]
                ok = sliced == sec_norm
                snippet = sec_norm[:60].replace("\n", " ")
                result.et_offset_samples.append((sec_name, ok, snippet))

    except Exception as exc:  # noqa: BLE001
        result.et_error = str(exc)


# ── sec-parser probe ──────────────────────────────────────────────────────────

def _probe_sec_parser(
    html: str, form_type: str, result: FilingResult
) -> None:
    try:
        import sec_parser as sp

        parser = sp.Edgar10QParser()
        elements = parser.parse(html)

        # Collect top-section titles
        titles: list[str] = []
        for el in elements:
            if isinstance(el, (sp.TopSectionTitle, sp.TitleElement)):
                titles.append(el.text.strip())

        result.sp_sections_found = titles

        # TOC false-positive: a title element whose text matches a TOC pattern
        for t in titles:
            if _is_toc_line(t):
                result.sp_toc_false_positives.append(t)

        # Table detection: count TableElement instances
        result.sp_tables_detected = sum(
            1 for el in elements if isinstance(el, sp.TableElement)
        )

    except Exception as exc:  # noqa: BLE001
        result.sp_error = str(exc)


# ── Required sections check ───────────────────────────────────────────────────

def _required_sections_present(
    sections: list[str], form_type: str
) -> tuple[bool, list[str]]:
    """Return (all_present, list_of_missing_canonical_names).

    Each required entry is a tuple of aliases; a section is "found" if any
    alias is a case-insensitive prefix of any detected section label.
    """
    required = REQUIRED_10K_SECTIONS if "10-K" in form_type else REQUIRED_10Q_SECTIONS
    sections_lower = [s.lower() for s in sections]
    missing: list[str] = []
    for aliases in required:
        canonical = aliases[0]
        if not any(
            sec.startswith(alias) or alias in sec
            for sec in sections_lower
            for alias in aliases
        ):
            missing.append(canonical)
    return len(missing) == 0, missing


# ── Main ──────────────────────────────────────────────────────────────────────

def run_spike() -> list[FilingResult]:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    results: list[FilingResult] = []

    with EdgarClient(cache_dir=CACHE_DIR) as client:
        for cik, ticker in TARGETS:
            print(f"\n{'='*60}")
            print(f"  {ticker}  (CIK {cik})")
            print(f"{'='*60}")
            filings = client.list_filings(cik, max_filings=40)

            # Pick the most-recent 10-K and most-recent 10-Q
            tenk = next((f for f in filings if f.form_type == "10-K"), None)
            tenq = next((f for f in filings if f.form_type == "10-Q"), None)

            for filing in [tenk, tenq]:
                if filing is None:
                    print(f"  WARNING: no filing found for {ticker}")
                    continue

                print(f"  {filing.form_type}  {filing.accession_no}  {filing.fiscal_period}")
                html_path = client.download_filing(filing)
                html = html_path.read_text(encoding="utf-8", errors="replace")

                # XBRL dei cross-check
                dei_period = _extract_dei_fiscal_period(html, filing.form_type)
                period_match: bool | None
                if dei_period.startswith("ERROR:") or not dei_period:
                    period_match = None
                else:
                    period_match = (dei_period == filing.fiscal_period)

                res = FilingResult(
                    ticker=ticker,
                    accession_no=filing.accession_no,
                    form_type=filing.form_type,
                    fiscal_period_derived=filing.fiscal_period,
                    fiscal_period_xbrl=dei_period,
                    fiscal_period_match=period_match,
                )

                _probe_edgartools(html, filing.form_type, res)
                _probe_sec_parser(html, filing.form_type, res)
                results.append(res)

                # Progress summary
                print(f"    edgartools sections : {res.et_sections_found[:6]}")
                print(f"    edgartools tables   : {res.et_tables_detected}")
                print(f"    sec-parser sections : {res.sp_sections_found[:6]}")
                print(f"    sec-parser tables   : {res.sp_tables_detected}")
                print(f"    dei period match    : {period_match}  "
                      f"(derived={filing.fiscal_period}  xbrl={dei_period})")

    return results


def write_report(results: list[FilingResult]) -> None:
    lines: list[str] = []

    def w(s: str = "") -> None:
        lines.append(s)

    w("PARSER SPIKE REPORT")
    w("=" * 72)
    w("Tickers : NVDA, AMD, INTC")
    w("Filings : one 10-K + one 10-Q per ticker (6 total)")
    w("Parsers : edgartools (edgar.documents.parse_html)")
    w("          sec-parser (Edgar10QParser)")
    w("Checks  : TOC false positives, table survival, char-offset")
    w("          accuracy (edgartools), dei fiscal-period cross-check")
    w()

    # ── Per-filing detail ──────────────────────────────────────────────────
    for res in results:
        w("-" * 72)
        w(f"FILING  {res.ticker}  {res.form_type}  {res.accession_no}")
        w(f"  Fiscal period (derived) : {res.fiscal_period_derived}")
        w(f"  Fiscal period (XBRL)    : {res.fiscal_period_xbrl or '(unavailable)'}")
        match_str = {True: "MATCH", False: "MISMATCH", None: "N/A"}[res.fiscal_period_match]
        w(f"  Fiscal period check     : {match_str}")
        w()

        # edgartools
        w("  ── edgartools ───────────────────────────────────────────────")
        if res.et_error:
            w(f"  ERROR: {res.et_error}")
        else:
            et_ok, et_missing = _required_sections_present(
                res.et_sections_found, res.form_type
            )
            miss_str = ", ".join(et_missing) if et_missing else ""
            w(f"  Sections found ({len(res.et_sections_found)})  "
              f"required present: {'YES' if et_ok else 'NO — missing: ' + miss_str}")
            w(f"  Sections list  : {res.et_sections_found}")
            toc_fp = res.et_toc_false_positives
            w(f"  TOC false positives : {len(toc_fp)}  {toc_fp if toc_fp else ''}")
            w(f"  Tables detected     : {res.et_tables_detected}")
            w("  Offset round-trips  :")
            if res.et_offset_samples:
                for label, ok, snippet in res.et_offset_samples:
                    status = "OK   " if ok else "FAIL "
                    w(f"    {status} [{label}]  {snippet[:60]!r}")
            else:
                w("    (no samples)")
        w()

        # sec-parser
        w("  ── sec-parser ───────────────────────────────────────────────")
        if res.sp_error:
            w(f"  ERROR: {res.sp_error}")
        else:
            sp_ok, sp_missing = _required_sections_present(
                res.sp_sections_found, res.form_type
            )
            miss_str = ", ".join(sp_missing) if sp_missing else ""
            w(f"  Sections found ({len(res.sp_sections_found)})  "
              f"required present: {'YES' if sp_ok else 'NO — missing: ' + miss_str}")
            toc_fp = res.sp_toc_false_positives
            w(f"  TOC false positives : {len(toc_fp)}  {toc_fp if toc_fp else ''}")
            w(f"  Tables detected     : {res.sp_tables_detected}")
        w()

    # ── Fiscal period summary ─────────────────────────────────────────────
    w("=" * 72)
    w("FISCAL PERIOD CROSS-CHECK SUMMARY")
    w()
    mismatches = [r for r in results if r.fiscal_period_match is False]
    unavailable = [r for r in results if r.fiscal_period_match is None]
    matches = [r for r in results if r.fiscal_period_match is True]
    w(f"  Matches    : {len(matches)}")
    w(f"  Mismatches : {len(mismatches)}")
    w(f"  N/A (XBRL unavailable) : {len(unavailable)}")
    if mismatches:
        w()
        w("  MISMATCHES:")
        for r in mismatches:
            w(f"    {r.ticker} {r.form_type}  derived={r.fiscal_period_derived}"
              f"  xbrl={r.fiscal_period_xbrl}")
    w()

    # ── Overall summary ───────────────────────────────────────────────────
    w("=" * 72)
    w("OVERALL SUMMARY")
    w()

    et_errors = [r for r in results if r.et_error]
    sp_errors = [r for r in results if r.sp_error]
    et_toc_total = sum(len(r.et_toc_false_positives) for r in results)
    sp_toc_total = sum(len(r.sp_toc_false_positives) for r in results)
    et_table_total = sum(r.et_tables_detected for r in results)
    sp_table_total = sum(r.sp_tables_detected for r in results)

    offset_oks = sum(
        1 for r in results for _, ok, _ in r.et_offset_samples if ok
    )
    offset_total = sum(len(r.et_offset_samples) for r in results)

    w(f"  edgartools errors         : {len(et_errors)}")
    w(f"  sec-parser errors         : {len(sp_errors)}")
    w(f"  edgartools TOC FPs        : {et_toc_total}")
    w(f"  sec-parser  TOC FPs       : {sp_toc_total}")
    w(f"  edgartools tables total   : {et_table_total}")
    w(f"  sec-parser  tables total  : {sp_table_total}")
    w(f"  edgartools offset OK      : {offset_oks}/{offset_total}")
    w()

    # ── Recommendation ────────────────────────────────────────────────────
    w("=" * 72)
    w("RECOMMENDATION")
    w()
    w("  [To be filled in after reviewing results above]")
    w()

    if mismatches:
        w("  FISCAL PERIOD: dei tags disagree with derive_fiscal_period() for")
        w("  the filing(s) listed above.  Recommend switching to dei tags as")
        w("  the primary source of truth and falling back to the algorithm only")
        w("  when tags are absent.")
    elif unavailable:
        w("  FISCAL PERIOD: dei tags were unavailable for some filings.  The")
        w("  derive_fiscal_period() algorithm produced results that could not")
        w("  be cross-checked.  Consider adding explicit validation for filings")
        w("  that do expose dei tags.")
    else:
        w("  FISCAL PERIOD: all available dei tags match derive_fiscal_period().")
        w("  The algorithm is correct for these tickers; retain it as-is.")
    w()

    # ── python-dotenv justification ───────────────────────────────────────
    w("=" * 72)
    w("DEPENDENCY JUSTIFICATION")
    w()
    w("  python-dotenv (spike group only, not in production dependencies)")
    w("    Justification: spike scripts and local developer tooling need to")
    w("    load credentials (EDGAR_USER_AGENT, etc.) from .env without")
    w("    requiring the caller to export them manually.  python-dotenv is")
    w("    the standard, zero-dependency solution; it is listed under the")
    w("    [spike] dependency group and is never imported by ingest/ code")
    w("    that runs in production or CI.")
    w()

    text = "\n".join(lines)
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(f"\nReport written to {REPORT_PATH}")
    print(text)


if __name__ == "__main__":
    results = run_spike()
    write_report(results)
