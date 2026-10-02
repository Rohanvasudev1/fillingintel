"""Tests for the full-corpus run: manifest selection, section report, table accounting."""
from datetime import date

import httpx
import pytest

from ingest.corpus import (
    build_report,
    build_reports,
    classify_tables,
    format_report,
    select_manifest,
)
from ingest.models import FilingMeta, ParsedTable
from ingest.parser import parse_filing, parse_filing_with_methods

VALID_METHODS = {"edgartools", "heading", "cross_reference_index"}


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

class TestSelectManifest:
    def _filings(self) -> list[FilingMeta]:
        out: list[FilingMeta] = []
        for cik in ("1", "2"):
            for i in range(4):
                out.append(_meta(cik, i, "10-K", date(2020 + i, 12, 31)))
            for i in range(9):
                out.append(_meta(cik, 100 + i, "10-Q", date(2020 + i // 3, 3 + 3 * (i % 3), 28)))
        return out

    def test_two_newest_10k_and_six_newest_10q_per_company(self):
        picked = select_manifest(self._filings())
        for cik in ("1", "2"):
            ks = [f for f in picked if f.cik == cik and f.form_type == "10-K"]
            qs = [f for f in picked if f.cik == cik and f.form_type == "10-Q"]
            assert [f.report_date.year for f in ks] == [2023, 2022]
            assert len(qs) == 6
            assert min(q.report_date for q in qs) > date(2020, 12, 31)
        assert len(picked) == 16

    def test_fewer_filings_than_quota_returns_all(self):
        few = [_meta("1", 1, "10-K", date(2024, 12, 31))]
        assert select_manifest(few) == few

    def test_input_not_mutated(self):
        filings = self._filings()
        before = list(filings)
        select_manifest(filings)
        assert filings == before


# ── classify_tables ───────────────────────────────────────────────────────────

class TestClassifyTables:
    def test_table_whose_row_labels_are_in_text_is_inserted(self):
        tbl = ParsedTable(
            index=0,
            markdown="| Net income | 1 |\n| Operating income | 2 |",
            section_key=None,
        )
        text = "intro\n| Net income | $ | 1 |\n| Operating income | 2 |\n"
        counts = classify_tables([tbl], text)
        assert (counts.detected, counts.inserted) == (1, 1)

    def test_table_absent_from_text_is_not_inserted(self):
        tbl = ParsedTable(
            index=0,
            markdown="| Net income | 1 |\n| Operating income | 2 |",
            section_key=None,
        )
        counts = classify_tables([tbl], "nothing relevant here")
        assert (counts.detected, counts.inserted, counts.not_inserted) == (1, 0, 1)

    def test_numeric_only_table_is_indeterminate(self):
        tbl = ParsedTable(index=0, markdown="| 1 | 2 |\n| 3 | 4 |", section_key=None)
        counts = classify_tables([tbl], "| 1 | 2 |")
        assert counts.indeterminate == 1
        assert counts.inserted == 0

    def test_counts_partition_detected(self, nvda_10q_filing):
        c = classify_tables(nvda_10q_filing.tables, nvda_10q_filing.text)
        assert c.detected == len(nvda_10q_filing.tables)
        assert c.inserted + c.not_inserted + c.indeterminate == c.detected
        assert c.inserted > 0


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
