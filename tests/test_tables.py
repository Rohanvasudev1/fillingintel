"""Tests for table spans: tables located by their position in the parsed text (Step 3a)."""
import pytest

from ingest.models import ParsedFiling, ParsedSection, ParsedTable
from ingest.tables import (
    TableSpan,
    find_table_spans,
    reconcile_tables,
    tables_crossing_sections,
)
from tests.table_checks import (
    assert_edgartools_tables_reconcile,
    assert_every_table_line_is_in_a_span,
    assert_no_span_starts_at_a_separator_row,
    assert_no_table_crosses_a_section,
    span_containing,
)

FIXTURE_FILINGS = [
    "nvda_10k_filing",
    "nvda_10q_filing",
    "amd_10k_filing",
    "amd_10q_filing",
    "intc_10k_filing",
    "intc_10q_filing",
]


def _filing(
    text: str,
    bounds: list[tuple[str, int, int]] | None = None,
    tables: list[str] | None = None,
) -> ParsedFiling:
    """A filing over *text*; one section covering all of it unless *bounds* are given.

    *tables* are edgartools' renderings of the filing's tables, in document order.
    """
    bounds = bounds or [("part_i_item_1", 0, len(text))]
    return ParsedFiling(
        accession_no="0000000000-26-000001",
        cik="1",
        form_type="10-Q",
        fiscal_period="FY2026-Q1",
        text=text,
        sections=[ParsedSection(label=lbl, char_start=s, char_end=e) for lbl, s, e in bounds],
        tables=[ParsedTable(index=i, markdown=md) for i, md in enumerate(tables or [])],
    )


def _span_texts(filing: ParsedFiling) -> list[str]:
    return [filing.text[s.char_start : s.char_end] for s in find_table_spans(filing)]


# ── Row and block rules on small markdown strings ─────────────────────────────

class TestRowsAndBlocks:
    def test_single_table_is_one_span_with_exact_text(self):
        table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        filing = _filing(f"Intro paragraph.\n\n{table}\n\nAfter.")
        assert _span_texts(filing) == [table]

    def test_span_records_rows_and_section(self):
        filing = _filing("| a | b |\n| --- | --- |\n| 1 | 2 |")
        (span,) = find_table_spans(filing)
        assert (span.rows, span.unclosed_rows) == (3, 0)
        assert (span.section, span.section_index) == ("part_i_item_1", 0)

    def test_cell_with_line_breaks_stays_in_one_row(self):
        # Shape seen in the INTC 10-Q exhibit index header.
        table = "| Exhibit\n\n\nNumber | Exhibit Description |\n| 3.1 | Charter |"
        filing = _filing(f"Text.\n\n{table}\n\nMore text.")
        assert _span_texts(filing) == [table]
        (span,) = find_table_spans(filing)
        assert span.rows == 2

    def test_blank_line_separates_two_tables(self):
        first = "| Consolidated Balance Sheets |"
        second = "| Assets | 1 |\n| Liabilities | 2 |"
        filing = _filing(f"{first}\n\n{second}")
        assert _span_texts(filing) == [first, second]

    def test_unclosed_row_ends_at_its_own_line(self):
        # Shape seen in the NVDA 10-K: a table row runs into a prose paragraph.
        row = "| Income before income tax | $ | 141,450 | The income tax expense differs"
        prose = "from the amount computed by applying the statutory rate.\n\nNext paragraph."
        filing = _filing(f"| Header | x |\n{row}\n{prose}")
        (span,) = find_table_spans(filing)
        assert filing.text[span.char_start : span.char_end] == f"| Header | x |\n{row}"
        assert span.unclosed_rows == 1

    def test_header_row_glued_to_prose_is_part_of_the_table(self):
        # Shape seen in INTC 10-Qs: edgartools prints the header row at the end of
        # the paragraph before the table, so the table's own lines start at "| --- |".
        prose = "Corporate unallocated expenses include restructuring charges\\."
        header = "| (In Millions) | Three Months Ended |"
        rest = "| --- | --- |\n| Restructuring | 4,070 |"
        filing = _filing(f"{prose} {header}\n{rest}\n\nAfter.")
        (span,) = find_table_spans(filing)
        assert filing.text[span.char_start : span.char_end] == f"{header}\n{rest}"
        assert span.header_glued

    def test_glued_header_ignores_an_earlier_pipe_in_the_prose(self):
        prose = "Revenue was up | down by segment."
        header = "| (In Millions) | Q1 |"
        filing = _filing(f"{prose} {header}\n| --- | --- |\n| Revenue | 5 |")
        (span,) = find_table_spans(filing)
        assert filing.text[span.char_start :].startswith(header)

    def test_prose_ending_in_a_pipe_before_a_headed_table_is_not_glued(self):
        filing = _filing("Values are shown as a | b |\n| a | b |\n| --- | --- |\n| 1 | 2 |")
        (span,) = find_table_spans(filing)
        assert filing.text[span.char_start :].startswith("| a | b |")
        assert not span.header_glued

    def test_pipe_inside_a_prose_line_is_not_a_table(self):
        filing = _filing("Revenue was up | down depending on the segment.\n\nNo tables here.")
        assert find_table_spans(filing) == ()

    def test_table_at_end_of_text_without_trailing_newline(self):
        filing = _filing("Intro.\n\n| a | b |")
        assert _span_texts(filing) == ["| a | b |"]

    def test_spans_are_found_within_each_section(self):
        text = "| a |\n\n| b |"
        filing = _filing(text, [("part_i_item_1", 0, 5), ("part_i_item_2", 7, 12)])
        spans = find_table_spans(filing)
        assert [(s.section, s.section_index) for s in spans] == [
            ("part_i_item_1", 0),
            ("part_i_item_2", 1),
        ]

    def test_table_cut_by_a_section_boundary_is_reported(self):
        text = "| a |\n| b |\n| c |"
        filing = _filing(text, [("part_i_item_1", 0, 11), ("part_i_item_2", 12, 17)])
        assert tables_crossing_sections(filing) == ((0, 17),)

    def test_table_span_rejects_end_before_start(self):
        with pytest.raises(ValueError):
            TableSpan(
                char_start=10, char_end=10, section="x", section_index=0, rows=1, unclosed_rows=0
            )


# ── Reconciling edgartools' table list with the spans ────────────────────────

class TestReconcileTables:
    def test_each_table_in_its_own_span(self):
        a, b = "| a | b |\n| --- | --- |", "| c |"
        rec = reconcile_tables(_filing(f"{a}\n\nText.\n\n{b}", tables=[a, b]))
        assert (rec.edgartools_tables, rec.span_count, rec.sharing_a_span) == (2, 2, 0)

    def test_table_rendered_empty_is_counted(self):
        rec = reconcile_tables(_filing("| a |", tables=["", "| a |"]))
        assert (rec.rendered_empty, rec.span_count) == (1, 1)

    def test_adjacent_tables_share_a_span(self):
        a, b = "| a |\n| --- |", "| b |\n| --- |"
        rec = reconcile_tables(_filing(f"{a}\n{b}", tables=[a, b]))
        assert (rec.span_count, rec.sharing_a_span) == (1, 1)

    def test_span_with_no_edgartools_table_is_counted(self):
        rec = reconcile_tables(_filing("| Title row |\n\n| a |", tables=["| a |"]))
        assert (rec.span_count, rec.spans_without_table) == (2, 1)

    def test_rendered_table_missing_from_text_is_counted(self):
        rec = reconcile_tables(_filing("| a |", tables=["| a |", "| zzz |"]))
        assert rec.not_in_text == 1

    def test_rendered_table_starting_outside_every_span_is_counted(self):
        # A rendering that starts in prose, as a header glued to prose would
        # without the glued-header rule.
        rec = reconcile_tables(_filing("Prose | x |\n\nMore.", tables=["| x |"]))
        assert rec.outside_spans == 1


# ── Acceptance checks on the six real fixture filings ─────────────────────────

@pytest.mark.parametrize("name", FIXTURE_FILINGS)
class TestFixtureFilings:
    def test_no_table_crosses_a_section_boundary(self, name, request):
        assert_no_table_crosses_a_section(request.getfixturevalue(name))

    def test_every_span_lies_inside_its_section(self, name, request):
        filing = request.getfixturevalue(name)
        spans = find_table_spans(filing)
        assert spans, "every fixture filing has tables"
        for span in spans:
            section = filing.sections[span.section_index]
            assert section.label == span.section
            assert section.char_start <= span.char_start < span.char_end <= section.char_end

    def test_spans_are_ordered_and_disjoint(self, name, request):
        spans = find_table_spans(request.getfixturevalue(name))
        for prev, nxt in zip(spans, spans[1:]):
            assert prev.char_end < nxt.char_start

    def test_every_table_line_is_inside_a_span(self, name, request):
        assert_every_table_line_is_in_a_span(request.getfixturevalue(name))

    def test_no_span_starts_at_a_separator_row(self, name, request):
        assert_no_span_starts_at_a_separator_row(request.getfixturevalue(name))

    def test_edgartools_tables_reconcile_with_spans(self, name, request):
        assert_edgartools_tables_reconcile(request.getfixturevalue(name))

    def test_closed_spans_start_and_end_with_a_pipe(self, name, request):
        filing = request.getfixturevalue(name)
        for span in find_table_spans(filing):
            body = filing.text[span.char_start : span.char_end]
            assert body.startswith("|")
            if span.unclosed_rows == 0:
                assert body.rstrip().endswith("|")


class TestPinnedTables:
    def test_nvda_10k_fiscal_summary_is_one_span_in_mdna(self, nvda_10k_filing):
        text = nvda_10k_filing.text
        pos = text.index("| Revenue | $ | 215,938 |")
        span = span_containing(nvda_10k_filing, pos)
        body = text[span.char_start : span.char_end]
        assert span.section == "part_ii_item_7"
        assert body.startswith("|  | Jan 25, 2026 |")
        assert body.endswith(
            "| Net income per diluted share | $ | 4.90 |  | $ | 2.94 |  | Up 67% |"
        )

    def test_nvda_10k_has_the_unclosed_income_tax_row(self, nvda_10k_filing):
        assert sum(s.unclosed_rows for s in find_table_spans(nvda_10k_filing)) >= 1

    def test_intc_10q_exhibit_header_with_line_breaks_is_in_one_span(self, intc_10q_filing):
        text = intc_10q_filing.text
        pos = text.index("| Exhibit\n\n\nNumber |")
        span = span_containing(intc_10q_filing, pos)
        body = text[span.char_start : span.char_end]
        # The table's first row is "|  |  | Incorporated by Reference |", one line above.
        assert body.startswith("|  |  | Incorporated by Reference |")
        assert "Date | Filed or Furnished Herewith |\n| 3.1 |" in body

    def test_nvda_10k_unclosed_row_does_not_swallow_the_next_table(self, nvda_10k_filing):
        text = nvda_10k_filing.text
        pos = text.index("| Income before income tax | $ | 141,450")
        span = span_containing(nvda_10k_filing, pos)
        body = text[span.char_start : span.char_end]
        assert body.endswith(
            "before income taxes for the fiscal year ended January 25, 2026 as follows:"
        )
        assert "| US Federal Statutory Tax Rate |" not in body

    def test_intc_10q_glued_header_starts_the_restructuring_table(self, intc_10q_filing):
        text = intc_10q_filing.text
        pos = text.index("| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n| Restructuring")
        span = span_containing(intc_10q_filing, pos)
        assert span.header_glued
        assert text[span.char_start :].startswith("| (In Millions) | Three Months Ended |")
        assert text[span.char_start - 2 : span.char_start] == ". "
