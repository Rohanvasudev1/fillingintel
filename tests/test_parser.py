"""Tests for ingest/parser.py — revised Step 2b requirements.

RED phase: all integration tests must FAIL until parser is implemented with
the full fallback chain (edgartools → heading → cross_reference_index).

Key invariants:
  - Every required section is present in all 6 filings (not in missing_sections).
  - text[char_start:char_end] == section text exactly (round-trip).
  - Tables embedded inline in section text (pipe chars present in financial sections).
  - fallback_sections tracks keys found via heading/cross_reference, not edgartools.
  - No two sections overlap.
  - No section is empty.
"""
import logging

from ingest.parser import (
    SECTION_ORDER_10K,
    SECTION_ORDER_10Q,
    _sort_key,
)

# ── Unit: canonical sort order ────────────────────────────────────────────────


class TestSectionOrder:
    def test_section_order_sorting_unit(self):
        keys = ["part_ii_item_8", "part_i_item_1a", "part_i_item_1"]
        sorted_keys = sorted(keys, key=lambda k: _sort_key(k, SECTION_ORDER_10K))
        assert sorted_keys == ["part_i_item_1", "part_i_item_1a", "part_ii_item_8"]

    def test_unknown_section_key_sorts_to_end(self):
        keys = ["part_i_item_1", "zz_unknown_section"]
        sorted_keys = sorted(keys, key=lambda k: _sort_key(k, SECTION_ORDER_10K))
        assert sorted_keys == ["part_i_item_1", "zz_unknown_section"]

    def test_10k_order_has_required_sections(self):
        assert "part_i_item_1a" in SECTION_ORDER_10K
        assert "part_ii_item_7" in SECTION_ORDER_10K
        assert "part_ii_item_8" in SECTION_ORDER_10K

    def test_10q_order_has_required_sections(self):
        assert "part_i_item_1" in SECTION_ORDER_10Q
        assert "part_i_item_2" in SECTION_ORDER_10Q
        assert "part_ii_item_1a" in SECTION_ORDER_10Q


# ── Tables as edgartools renders them ─────────────────────────────────────────


class TestParsedTables:
    def test_tables_are_edgartools_renderings_found_in_the_text(self, nvda_10k_filing):
        rendered = [t.markdown for t in nvda_10k_filing.tables if t.markdown]
        assert rendered
        assert all(md in nvda_10k_filing.text for md in rendered)

    def test_tables_edgartools_renders_empty_are_kept(self, amd_10q_filing):
        """AMD's 10-Q has 4 layout tables with no content columns; they stay in the count."""
        assert sum(1 for t in amd_10q_filing.tables if not t.markdown) == 4
        assert [t.index for t in amd_10q_filing.tables] == list(range(len(amd_10q_filing.tables)))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _round_trip_check(filing) -> None:
    """Verify text[char_start:char_end] == section text for every section."""
    assert len(filing.sections) > 0
    parts = [filing.text[s.char_start:s.char_end] for s in filing.sections]
    assert "\n\n".join(parts) == filing.text


def _no_overlap_check(filing) -> None:
    secs = sorted(filing.sections, key=lambda s: s.char_start)
    for a, b in zip(secs, secs[1:]):
        assert a.char_end <= b.char_start, (
            f"Overlap: {a.label} [{a.char_start}:{a.char_end}] "
            f"overlaps {b.label} [{b.char_start}:{b.char_end}]"
        )


def _no_empty_sections(filing) -> None:
    for sec in filing.sections:
        text = filing.text[sec.char_start:sec.char_end]
        assert text.strip(), f"Section {sec.label} is empty"


# ── Integration: NVDA 10-K ────────────────────────────────────────────────────


class TestNvda10K:
    def test_no_exception(self, nvda_10k_filing):
        assert nvda_10k_filing is not None

    def test_round_trip(self, nvda_10k_filing):
        _round_trip_check(nvda_10k_filing)

    def test_no_overlap(self, nvda_10k_filing):
        _no_overlap_check(nvda_10k_filing)

    def test_no_empty_sections(self, nvda_10k_filing):
        _no_empty_sections(nvda_10k_filing)

    def test_section_order(self, nvda_10k_filing):
        labels = [s.label for s in nvda_10k_filing.sections]
        assert labels[0] == "preamble"  # text before the first item, kept for full coverage
        labels = labels[1:]
        for a, b in zip(labels, labels[1:]):
            a_idx = SECTION_ORDER_10K.index(a) if a in SECTION_ORDER_10K else len(SECTION_ORDER_10K)
            b_idx = SECTION_ORDER_10K.index(b) if b in SECTION_ORDER_10K else len(SECTION_ORDER_10K)
            assert a_idx <= b_idx, f"Out of order: {a} before {b}"

    def test_no_missing_required(self, nvda_10k_filing):
        """NVDA 10-K has all required sections — none genuinely absent."""
        assert nvda_10k_filing.missing_sections == []

    def test_required_sections_present(self, nvda_10k_filing):
        labels = {s.label for s in nvda_10k_filing.sections}
        for key in ("part_i_item_1a", "part_ii_item_7", "part_ii_item_8"):
            assert key in labels, f"Required section {key} missing from NVDA 10-K"

    def test_tables_have_pipes(self, nvda_10k_filing):
        """Financial sections contain pipe-delimited table content.

        NVDA Item 8 is a brief cross-reference (financial statements are in
        Item 15 exhibits). Item 7 (MD&A) contains the financial tables.
        """
        item7 = next(
            (s for s in nvda_10k_filing.sections if s.label == "part_ii_item_7"), None
        )
        assert item7 is not None
        text = nvda_10k_filing.text[item7.char_start:item7.char_end]
        assert "|" in text, "part_ii_item_7 must contain table markdown"

    def test_no_fallback_needed(self, nvda_10k_filing):
        """NVDA 10-K: all sections via edgartools, none via fallback."""
        assert nvda_10k_filing.fallback_sections == []


# ── Integration: INTC 10-K ────────────────────────────────────────────────────


class TestIntc10K:
    def test_no_exception(self, intc_10k_filing):
        assert intc_10k_filing is not None

    def test_round_trip(self, intc_10k_filing):
        _round_trip_check(intc_10k_filing)

    def test_no_overlap(self, intc_10k_filing):
        _no_overlap_check(intc_10k_filing)

    def test_no_empty_sections(self, intc_10k_filing):
        _no_empty_sections(intc_10k_filing)

    def test_no_missing_required(self, intc_10k_filing):
        """INTC 10-K required sections are found via fallback — not genuinely absent."""
        assert intc_10k_filing.missing_sections == []

    def test_required_sections_present(self, intc_10k_filing):
        labels = {s.label for s in intc_10k_filing.sections}
        for key in ("part_i_item_1a", "part_ii_item_7", "part_ii_item_8"):
            assert key in labels, f"Required section {key} missing from INTC 10-K"

    def test_item7_via_cross_reference(self, intc_10k_filing):
        """part_ii_item_7 is found via cross_reference_index, not edgartools."""
        assert "part_ii_item_7" in intc_10k_filing.fallback_sections

    def test_item7_not_starting_in_cross_ref_table(self, intc_10k_filing):
        """Extracted INTC Item 7 must not start inside the cross-reference table."""
        sec = next(s for s in intc_10k_filing.sections if s.label == "part_ii_item_7")
        text = intc_10k_filing.text[sec.char_start:sec.char_end]
        # Cross-ref table contains page numbers and titles like "| Item 7. | Management's ... |"
        assert "Item 7." not in text[:500], (
            "Item 7 text starts inside cross-reference table"
        )

    def test_item7_starts_at_mda_heading(self, intc_10k_filing):
        """Extracted INTC Item 7 starts at Intel's MD&A section heading."""
        sec = next(s for s in intc_10k_filing.sections if s.label == "part_ii_item_7")
        text = intc_10k_filing.text[sec.char_start:sec.char_end]
        # Should start with the single-cell section heading or immediately before content
        first_300 = text[:300]
        assert any(
            kw in first_300
            for kw in ("Management", "MD&A", "Overview", "Discussion")
        ), f"Item 7 doesn't start at MD&A heading. First 300:\n{first_300}"

    def test_item7_first_last_300(self, intc_10k_filing):
        """Print first/last 300 chars of INTC 10-K Item 7 for manual verification."""
        sec = next(s for s in intc_10k_filing.sections if s.label == "part_ii_item_7")
        text = intc_10k_filing.text[sec.char_start:sec.char_end]
        print(f"\nINTC 10-K Item 7 first 300:\n{text[:300]!r}")
        print(f"\nINTC 10-K Item 7 last 300:\n{text[-300:]!r}")
        # Not a test assertion — informational

    def test_tables_in_financial_section(self, intc_10k_filing):
        item8 = next(
            (s for s in intc_10k_filing.sections if s.label == "part_ii_item_8"), None
        )
        assert item8 is not None
        text = intc_10k_filing.text[item8.char_start:item8.char_end]
        assert "|" in text


# ── Integration: NVDA 10-Q ────────────────────────────────────────────────────


class TestNvda10Q:
    def test_no_exception(self, nvda_10q_filing):
        assert nvda_10q_filing is not None

    def test_round_trip(self, nvda_10q_filing):
        _round_trip_check(nvda_10q_filing)

    def test_no_overlap(self, nvda_10q_filing):
        _no_overlap_check(nvda_10q_filing)

    def test_no_empty_sections(self, nvda_10q_filing):
        _no_empty_sections(nvda_10q_filing)

    def test_no_missing_required(self, nvda_10q_filing):
        """NVDA 10-Q: part_ii_item_1a found via heading fallback — not genuinely absent."""
        assert nvda_10q_filing.missing_sections == []

    def test_required_sections_present(self, nvda_10q_filing):
        labels = {s.label for s in nvda_10q_filing.sections}
        for key in ("part_i_item_1", "part_i_item_2", "part_ii_item_1a"):
            assert key in labels, f"Required section {key} missing from NVDA 10-Q"

    def test_item_1a_via_fallback(self, nvda_10q_filing):
        """part_ii_item_1a found via heading (not edgartools) for NVDA 10-Q."""
        assert "part_ii_item_1a" in nvda_10q_filing.fallback_sections

    def test_tables_in_financial_section(self, nvda_10q_filing):
        item1 = next(
            (s for s in nvda_10q_filing.sections if s.label == "part_i_item_1"), None
        )
        assert item1 is not None
        text = nvda_10q_filing.text[item1.char_start:item1.char_end]
        assert "|" in text


# ── Integration: AMD 10-K ─────────────────────────────────────────────────────


class TestAmd10K:
    def test_no_exception(self, amd_10k_filing):
        assert amd_10k_filing is not None

    def test_round_trip(self, amd_10k_filing):
        _round_trip_check(amd_10k_filing)

    def test_no_overlap(self, amd_10k_filing):
        _no_overlap_check(amd_10k_filing)

    def test_no_empty_sections(self, amd_10k_filing):
        _no_empty_sections(amd_10k_filing)

    def test_no_missing_required(self, amd_10k_filing):
        assert amd_10k_filing.missing_sections == []

    def test_required_sections_present(self, amd_10k_filing):
        labels = {s.label for s in amd_10k_filing.sections}
        for key in ("part_i_item_1a", "part_ii_item_7", "part_ii_item_8"):
            assert key in labels, f"Required section {key} missing from AMD 10-K"

    def test_tables_in_financial_section(self, amd_10k_filing):
        item8 = next(
            (s for s in amd_10k_filing.sections if s.label == "part_ii_item_8"), None
        )
        assert item8 is not None
        text = amd_10k_filing.text[item8.char_start:item8.char_end]
        assert "|" in text


# ── Integration: AMD 10-Q ─────────────────────────────────────────────────────


class TestAmd10Q:
    def test_no_exception(self, amd_10q_filing):
        assert amd_10q_filing is not None

    def test_round_trip(self, amd_10q_filing):
        _round_trip_check(amd_10q_filing)

    def test_no_overlap(self, amd_10q_filing):
        _no_overlap_check(amd_10q_filing)

    def test_no_empty_sections(self, amd_10q_filing):
        _no_empty_sections(amd_10q_filing)

    def test_no_missing_required(self, amd_10q_filing):
        assert amd_10q_filing.missing_sections == []

    def test_required_sections_present(self, amd_10q_filing):
        labels = {s.label for s in amd_10q_filing.sections}
        for key in ("part_i_item_1", "part_i_item_2", "part_ii_item_1a"):
            assert key in labels, f"Required section {key} missing from AMD 10-Q"

    def test_tables_in_financial_section(self, amd_10q_filing):
        item1 = next(
            (s for s in amd_10q_filing.sections if s.label == "part_i_item_1"), None
        )
        assert item1 is not None
        text = amd_10q_filing.text[item1.char_start:item1.char_end]
        assert "|" in text


# ── Integration: INTC 10-Q ────────────────────────────────────────────────────


class TestIntc10Q:
    def test_no_exception(self, intc_10q_filing):
        assert intc_10q_filing is not None

    def test_round_trip(self, intc_10q_filing):
        _round_trip_check(intc_10q_filing)

    def test_no_overlap(self, intc_10q_filing):
        _no_overlap_check(intc_10q_filing)

    def test_no_empty_sections(self, intc_10q_filing):
        _no_empty_sections(intc_10q_filing)

    def test_no_missing_required(self, intc_10q_filing):
        """INTC 10-Q: MD&A and Risk Factors found via fallback — not genuinely absent."""
        assert intc_10q_filing.missing_sections == []

    def test_required_sections_present(self, intc_10q_filing):
        labels = {s.label for s in intc_10q_filing.sections}
        for key in ("part_i_item_1", "part_i_item_2", "part_ii_item_1a"):
            assert key in labels, f"Required section {key} missing from INTC 10-Q"

    def test_fallback_sections_tracked(self, intc_10q_filing):
        """INTC 10-Q sections missed by edgartools are tracked in fallback_sections."""
        # MD&A and/or Risk Factors found via fallback
        assert len(intc_10q_filing.fallback_sections) > 0

    def test_tables_in_financial_section(self, intc_10q_filing):
        item1 = next(
            (s for s in intc_10q_filing.sections if s.label == "part_i_item_1"), None
        )
        assert item1 is not None
        text = intc_10q_filing.text[item1.char_start:item1.char_end]
        assert "|" in text


# ── Integration: warnings logged for missing required sections ────────────────


class TestWarnings:
    def test_no_warnings_when_all_found(self, nvda_10k_html, nvda_10k_meta, caplog):
        """No WARNING emitted when all required sections are found."""
        from ingest.parser import parse_filing

        with caplog.at_level(logging.WARNING, logger="ingest.parser"):
            parse_filing(nvda_10k_html, nvda_10k_meta)

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_msgs == [], f"Unexpected warnings: {warning_msgs}"
