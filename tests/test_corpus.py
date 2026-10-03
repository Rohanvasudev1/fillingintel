"""Tests for the full-corpus run: manifest selection, section report, table accounting."""
from datetime import date

import httpx
import pytest

from ingest.chunker import MAX_CHUNK_TOKENS, chunk_filing
from ingest.corpus import (
    build_report,
    build_reports,
    format_report,
    select_manifest,
)
from ingest.models import FilingMeta
from ingest.parser import parse_filing, parse_filing_with_methods
from ingest.tables import find_table_spans, reconcile_tables

VALID_METHODS = {"edgartools", "heading", "cross_reference_index", "preamble"}


def _meta(cik: str, n: int, form: str, report: date) -> FilingMeta:
    return FilingMeta(
        cik=cik,
        accession_no=f"{int(cik):010d}-{report.year % 100:02d}-{n:06d}",
        form_type=form,
        company_name="X",
        fiscal_period="FY2025",
        report_date=report,
        filing_date=report,
        primary_document="x.htm",
    )


# ── select_manifest ───────────────────────────────────────────────────────────

def _meta_fp(cik: str, n: int, form: str, fiscal_period: str, report: date) -> FilingMeta:
    return FilingMeta(
        cik=cik,
        accession_no=f"{int(cik):010d}-{report.year % 100:02d}-{n:06d}",
        form_type=form,
        company_name="X",
        fiscal_period=fiscal_period,
        report_date=report,
        filing_date=report,
        primary_document="x.htm",
    )


class TestSelectManifest:
    """Two most recent complete fiscal years: each 10-K plus the three 10-Qs of that year."""

    def _company(self, cik: str) -> list[FilingMeta]:
        out: list[FilingMeta] = []
        n = 0
        for fy in (2023, 2024, 2025):
            n += 1
            out.append(_meta_fp(cik, n, "10-K", f"FY{fy}", date(fy, 12, 28)))
            for q in (1, 2, 3):
                n += 1
                out.append(_meta_fp(cik, n, "10-Q", f"FY{fy}-Q{q}", date(fy, 3 * q, 28)))
        # A fiscal year in progress: two 10-Qs, no 10-K yet.
        out.append(_meta_fp(cik, 90, "10-Q", "FY2026-Q1", date(2026, 3, 28)))
        out.append(_meta_fp(cik, 91, "10-Q", "FY2026-Q2", date(2026, 6, 28)))
        return out

    def test_two_complete_fiscal_years_per_company(self):
        picked = select_manifest(self._company("1") + self._company("2"))
        for cik in ("1", "2"):
            mine = [f for f in picked if f.cik == cik]
            assert len(mine) == 8
            assert sorted(f.fiscal_period for f in mine if f.form_type == "10-K") == [
                "FY2024", "FY2025",
            ]
            assert sorted(f.fiscal_period for f in mine if f.form_type == "10-Q") == [
                "FY2024-Q1", "FY2024-Q2", "FY2024-Q3", "FY2025-Q1", "FY2025-Q2", "FY2025-Q3",
            ]
        assert len(picked) == 16

    def test_fiscal_year_in_progress_is_excluded(self):
        picked = select_manifest(self._company("1"))
        assert not any(f.fiscal_period.startswith("FY2026") for f in picked)

    def test_order_is_by_fiscal_year_then_form_then_quarter(self):
        picked = select_manifest(self._company("1"))
        assert [f.fiscal_period for f in picked][:4] == [
            "FY2025", "FY2025-Q1", "FY2025-Q2", "FY2025-Q3",
        ]

    def test_missing_quarter_is_returned_short_and_logged(self, caplog):
        filings = [f for f in self._company("1") if f.fiscal_period != "FY2024-Q2"]
        with caplog.at_level("WARNING"):
            picked = select_manifest(filings)
        assert len(picked) == 7
        assert "FY2024" in caplog.text and "Q2" in caplog.text

    def test_duplicate_filings_for_a_period_use_the_earliest_and_warn(self, caplog):
        filings = self._company("1")
        dup = _meta_fp("1", 95, "10-Q", "FY2025-Q1", date(2025, 4, 15))
        with caplog.at_level("WARNING"):
            picked = select_manifest([*filings, dup])
        q1 = [f for f in picked if f.fiscal_period == "FY2025-Q1"]
        assert len(q1) == 1 and q1[0].filing_date == date(2025, 3, 28)
        assert "FY2025-Q1" in caplog.text

    def test_fewer_complete_years_than_quota_returns_what_exists(self):
        few = [_meta_fp("1", 1, "10-K", "FY2024", date(2024, 12, 31))]
        assert select_manifest(few) == few

    def test_input_not_mutated(self):
        filings = self._company("1")
        before = list(filings)
        select_manifest(filings)
        assert filings == before


# ── table counts ──────────────────────────────────────────────────────────────

class TestTableCounts:
    def test_counts_come_from_spans_and_reconciliation(self, nvda_10q_html, nvda_10q_meta):
        rep = build_report(nvda_10q_html, nvda_10q_meta)
        filing = parse_filing(nvda_10q_html, nvda_10q_meta)
        spans = find_table_spans(filing)
        assert rep.tables.reconciliation == reconcile_tables(filing)
        assert rep.tables.reconciliation.span_count == len(spans)
        assert rep.tables.unclosed_rows == sum(s.unclosed_rows for s in spans)
        assert rep.tables.glued_headers == sum(s.header_glued for s in spans)
        assert rep.tables.crossing_tables == 0

    def test_report_has_table_reconciliation_columns(self, nvda_10q_html, nvda_10q_meta):
        text = format_report([build_report(nvda_10q_html, nvda_10q_meta)])
        assert (
            "| Accession | Form | edgartools tables | Rendered empty | Sharing a span "
            "| Spans without a table | Table spans | Not in text | Outside spans "
            "| Unclosed rows | Glued headers | Crossing a section |"
        ) in text


class TestChunkCounts:
    def test_counts_come_from_the_chunker(self, nvda_10q_html, nvda_10q_meta):
        rep = build_report(nvda_10q_html, nvda_10q_meta)
        chunks = chunk_filing(parse_filing(nvda_10q_html, nvda_10q_meta))
        assert rep.chunks.total == len(chunks)
        assert rep.chunks.with_tables == sum(c.contains_table for c in chunks)
        assert rep.chunks.oversized == sum(c.token_count > MAX_CHUNK_TOKENS for c in chunks)
        assert rep.chunks.max_tokens == max(c.token_count for c in chunks)

    def test_report_has_chunk_columns(self, nvda_10q_html, nvda_10q_meta):
        text = format_report([build_report(nvda_10q_html, nvda_10q_meta)])
        assert "| Accession | Form | Chunks | With tables | Over 800 tokens | Max tokens |" in text


# ── parse_filing_with_methods ─────────────────────────────────────────────────

class TestParseFilingWithMethods:
    def test_methods_cover_exactly_the_parsed_sections(self, intc_10k_html, intc_10k_meta):
        filing, methods = parse_filing_with_methods(intc_10k_html, intc_10k_meta)
        assert set(methods) == {s.label for s in filing.sections}
        assert set(methods.values()) <= VALID_METHODS

    def test_fallback_sections_never_include_edgartools_positioned_keys(
        self, intc_10q_html, intc_10q_meta
    ):
        filing, methods = parse_filing_with_methods(intc_10q_html, intc_10q_meta)
        fallback = set(filing.fallback_sections)
        assert fallback  # INTC 10-Q needs the cross-reference index
        assert all(methods[k] != "edgartools" for k in fallback)
        assert {k for k, m in methods.items() if m == "cross_reference_index"} <= fallback

    def test_intc_10k_item_7_via_cross_reference_index(self, intc_10k_html, intc_10k_meta):
        _, methods = parse_filing_with_methods(intc_10k_html, intc_10k_meta)
        assert methods["part_ii_item_7"] == "cross_reference_index"



# ── build_report ──────────────────────────────────────────────────────────────

class TestBuildReport:
    def test_required_rows_and_lengths(self, nvda_10k_html, nvda_10k_meta):
        rep = build_report(nvda_10k_html, nvda_10k_meta)
        required = {r.key: r for r in rep.sections if r.required}
        assert set(required) == {"part_i_item_1a", "part_ii_item_7", "part_ii_item_8"}
        assert rep.missing_required == ()
        for r in required.values():
            assert r.method in VALID_METHODS
            assert r.length > 0

    def test_lengths_match_section_offsets(self, amd_10q_html, amd_10q_meta):
        rep = build_report(amd_10q_html, amd_10q_meta)
        filing = parse_filing(amd_10q_html, amd_10q_meta)
        by_label = {s.label: s.char_end - s.char_start for s in filing.sections}
        assert {r.key: r.length for r in rep.sections} == by_label

    def test_multi_span_item_is_summed_and_counted(self, intc_10k_html, intc_10k_meta):
        rep = build_report(intc_10k_html, intc_10k_meta)
        filing = parse_filing(intc_10k_html, intc_10k_meta)
        item7 = {r.key: r for r in rep.sections}["part_ii_item_7"]
        spans = [s for s in filing.sections if s.label == "part_ii_item_7"]
        assert item7.spans == len(spans) == 2
        assert item7.length == sum(s.char_end - s.char_start for s in spans)
        assert "(2 spans)" in format_report([rep])

    def test_short_flag_marks_the_known_stub(self, nvda_10k_html, nvda_10k_meta):
        rep = build_report(nvda_10k_html, nvda_10k_meta)
        by_key = {r.key: r for r in rep.sections}
        assert by_key["part_ii_item_8"].short  # 211-char pointer to Item 15
        assert not by_key["part_ii_item_7"].short

    @pytest.mark.parametrize("bad", ["", "<html></html>"])
    def test_unparseable_html_raises(self, bad, nvda_10k_meta):
        with pytest.raises(ValueError, match="suspiciously short"):
            build_report(bad, nvda_10k_meta)


class TestFormatReport:
    def test_header_lines_appear_under_title(self, nvda_10k_html, nvda_10k_meta):
        rep = build_report(nvda_10k_html, nvda_10k_meta)
        text = format_report([rep], header=("- commit: abc1234",))
        assert text.splitlines()[:3] == ["# Full-corpus parser run", "", "- commit: abc1234"]

    def test_short_sections_are_flagged_in_output(self, nvda_10k_html, nvda_10k_meta):
        text = format_report([build_report(nvda_10k_html, nvda_10k_meta)])
        assert "| part_ii_item_8 |" in text and "| 211 | yes |" in text


class _FakeClient:
    """Stands in for EdgarClient: serves one real fixture, fails for the rest."""

    def __init__(self, good: dict[str, object]) -> None:
        self._good = good

    def download_filing(self, filing: FilingMeta):
        if filing.accession_no in self._good:
            return self._good[filing.accession_no]
        raise httpx.ConnectError("boom")


class TestBuildReports:
    def test_one_failure_does_not_stop_the_run(self, tmp_path, nvda_10k_html, nvda_10k_meta):
        path = tmp_path / "ok.html"
        path.write_text(nvda_10k_html, encoding="utf-8")
        bad = _meta("1", 9, "10-K", date(2024, 12, 31))
        client = _FakeClient({nvda_10k_meta.accession_no: path})
        reports, failures = build_reports(client, [bad, nvda_10k_meta])
        assert [r.meta.accession_no for r in reports] == [nvda_10k_meta.accession_no]
        assert len(failures) == 1
        assert bad.accession_no in failures[0] and "ConnectError" in failures[0]

    def test_parse_failure_is_reported_with_accession(self, tmp_path, nvda_10k_meta):
        path = tmp_path / "empty.html"
        path.write_text("<html></html>", encoding="utf-8")
        client = _FakeClient({nvda_10k_meta.accession_no: path})
        reports, failures = build_reports(client, [nvda_10k_meta])
        assert reports == []
        assert nvda_10k_meta.accession_no in failures[0]


class TestRunHeaderAndMain:
    def test_run_header_names_commit_library_and_rules(self):
        from ingest.corpus import run_header

        header = "\n".join(run_header(24))
        assert "- commit:" in header and "- edgartools: 5." in header
        assert "24 filings" in header and "anchor tolerance 400" in header

    def test_main_writes_report_and_exits_nonzero_on_failure(
        self, monkeypatch, tmp_path, nvda_10k_html, nvda_10k_meta
    ):
        import ingest.corpus as corpus

        html_path = tmp_path / "ok.html"
        html_path.write_text(nvda_10k_html, encoding="utf-8")
        bad = _meta("1045810", 9, "10-K", date(2024, 12, 31))

        class FakeEdgarClient(_FakeClient):
            def __init__(self, _cache_dir):
                super().__init__({nvda_10k_meta.accession_no: html_path})

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def list_filings(self, cik):
                return [nvda_10k_meta, bad] if cik == "1045810" else []

        monkeypatch.setattr(corpus, "EdgarClient", FakeEdgarClient)
        out = tmp_path / "nested" / "report.txt"
        assert corpus.main([str(out)]) == 1
        text = out.read_text(encoding="utf-8")
        assert "## Failures" in text and bad.accession_no in text
        assert "- commit:" in text


class TestSectionRows:
    def test_two_spans_sum_and_count_in_document_order(self):
        from ingest.corpus import _section_rows
        from ingest.models import ParsedFiling, ParsedSection

        filing = ParsedFiling(
            accession_no="0000000001-25-000001", cik="1", form_type="10-K",
            fiscal_period="FY2025", text="x" * 100,
            sections=[
                ParsedSection(label="b", char_start=60, char_end=100),
                ParsedSection(label="a", char_start=0, char_end=20),
                ParsedSection(label="b", char_start=20, char_end=40),
            ],
        )
        rows = _section_rows(filing, {"a": "heading", "b": "edgartools"}, frozenset({"b"}))
        assert [(r.key, r.length, r.spans, r.required) for r in rows] == [
            ("a", 20, 1, False), ("b", 60, 2, True),
        ]
