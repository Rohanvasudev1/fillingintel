"""Table-span acceptance checks shared by the fixture tests and the local corpus tests."""
import re

from ingest.models import ParsedFiling
from ingest.tables import (
    SEPARATOR_ROW,
    TableSpan,
    find_table_spans,
    reconcile_tables,
    tables_crossing_sections,
)

_ROW_START = re.compile(r"^\|", re.M)


def span_containing(filing: ParsedFiling, pos: int) -> TableSpan:
    """The one table span that contains offset *pos*."""
    (span,) = [s for s in find_table_spans(filing) if s.char_start <= pos < s.char_end]
    return span


def assert_no_table_crosses_a_section(filing: ParsedFiling) -> None:
    assert tables_crossing_sections(filing) == ()


def assert_every_table_line_is_in_a_span(filing: ParsedFiling) -> None:
    spans = find_table_spans(filing)
    for section in filing.sections:
        body = filing.text[section.char_start : section.char_end]
        for m in _ROW_START.finditer(body):
            pos = section.char_start + m.start()
            assert any(s.char_start <= pos < s.char_end for s in spans), (
                f"{filing.accession_no}: table line at {pos} is in no span: "
                f"{filing.text[pos : pos + 80]!r}"
            )


def assert_no_span_starts_at_a_separator_row(filing: ParsedFiling) -> None:
    """A table's header row belongs to the table, even when edgartools glues it to prose."""
    for span in find_table_spans(filing):
        first = filing.text[span.char_start : span.char_end].split("\n", 1)[0]
        assert not SEPARATOR_ROW.fullmatch(first), f"{filing.accession_no}: {first!r}"


def assert_edgartools_tables_reconcile(filing: ParsedFiling) -> None:
    """Every table edgartools renders is in the text, inside a span, and the counts add up.

    This is evidence independent of the span scanner: edgartools' own list of
    tables, rendered by edgartools, located in the text.
    """
    rec = reconcile_tables(filing)
    assert rec.not_in_text == 0, f"{filing.accession_no}: {rec}"
    assert rec.outside_spans == 0, f"{filing.accession_no}: {rec}"
    assert rec.span_count == (
        rec.edgartools_tables
        - rec.rendered_empty
        - rec.sharing_a_span
        + rec.spans_without_table
    ), f"{filing.accession_no}: {rec}"
