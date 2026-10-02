"""Required sections must be real sections, not table-of-contents stubs.

Step 2b's first acceptance run only checked that each required section existed.
The full-corpus run showed several 'present' sections were 69-250 character
cross-reference stubs, or sat in the exhibit list.  These tests check substance:
length, and that the section starts at its own title.
"""
import re

import pytest

from ingest.corpus import MIN_SECTION_CHARS
from ingest.parser import (
    REQUIRED_SECTIONS_10K,
    REQUIRED_SECTIONS_10Q,
    _anchor_agrees,
    _find_statements_caption,
    _find_title_in_body,
    _heading_skeleton,
    _is_page_number_row,
    _snap_anchor,
    _snap_to_table_start,
    parse_filing_with_methods,
)

MIN_REQUIRED_CHARS = MIN_SECTION_CHARS

# Words that must appear in the first line of each required section.
START_KEYWORD = {
    "part_i_item_1a": "risk factors",
    "part_ii_item_1a": "risk factors",
    "part_ii_item_7": "management",
    "part_i_item_2": "management",
    "part_ii_item_8": "statements",
    "part_i_item_1": "statements",
}

# (fixture, section) pairs whose real content is a pointer to another Item.
# NVIDIA's Item 8 says the statements are set forth in Item 15.
POINTER_SECTIONS = {("nvda_10k_filing", "part_ii_item_8")}

FILINGS = [
    ("nvda_10k_filing", REQUIRED_SECTIONS_10K),
    ("intc_10k_filing", REQUIRED_SECTIONS_10K),
    ("amd_10k_filing", REQUIRED_SECTIONS_10K),
    ("nvda_10q_filing", REQUIRED_SECTIONS_10Q),
    ("intc_10q_filing", REQUIRED_SECTIONS_10Q),
    ("amd_10q_filing", REQUIRED_SECTIONS_10Q),
]
CASES = [(name, key) for name, required in FILINGS for key in sorted(required)]


def _text(filing, key: str) -> str:
    sec = next((s for s in filing.sections if s.label == key), None)
    assert sec is not None, f"section {key} missing"
    return filing.text[sec.char_start:sec.char_end]


@pytest.mark.parametrize(("fixture", "key"), CASES)
def test_required_section_is_substantial(request, fixture, key):
    if (fixture, key) in POINTER_SECTIONS:
        pytest.skip("documented pointer section; see test_pointer_sections_point")
    filing = request.getfixturevalue(fixture)
    assert len(_text(filing, key)) >= MIN_REQUIRED_CHARS


@pytest.mark.parametrize(("fixture", "key"), CASES)
def test_required_section_starts_at_its_title(request, fixture, key):
    filing = request.getfixturevalue(fixture)
    head = _text(filing, key).splitlines()[0].lower()
    assert START_KEYWORD[key] in head, f"{fixture}/{key} first line: {head!r}"


@pytest.mark.parametrize(("fixture", "key"), CASES)
def test_required_section_does_not_start_in_a_page_number_row(request, fixture, key):
    filing = request.getfixturevalue(fixture)
    first_line = _text(filing, key).splitlines()[0]
    assert not re.search(r"\|\s*\d{1,3}\s*\|\s*$", first_line), first_line


def test_pointer_sections_point(nvda_10k_filing):
    text = _text(nvda_10k_filing, "part_ii_item_8")
    assert "set forth" in text
    assert any(s.label == "part_iv_item_15" for s in nvda_10k_filing.sections)


# ── Specific boundaries that were wrong in the first full-corpus run ─────────

def test_amd_10q_item_1_starts_at_its_heading_not_the_exhibit_list(amd_10q_filing):
    text = _text(amd_10q_filing, "part_i_item_1")
    assert text.startswith("| ITEM 1.")
    assert len(text) > 50_000


def test_amd_10q_item_4_is_controls_and_procedures(amd_10q_filing):
    assert _text(amd_10q_filing, "part_i_item_4").startswith("| ITEM 4.")


def test_intc_10q_mdna_is_the_body_section(intc_10q_filing):
    text = _text(intc_10q_filing, "part_i_item_2")
    assert text.startswith("| Management's Discussion and Analysis |")
    assert len(text) > 50_000


def test_intc_10q_risk_factors_is_the_body_section(intc_10q_filing):
    assert _text(intc_10q_filing, "part_ii_item_1a").startswith(
        "| Risk Factors and Other Key Information |"
    )


def test_intc_10q_item_1_keeps_the_top_of_the_income_statement(intc_10q_filing):
    """The anchor used to land on a mid-table row, dropping 'Net revenue'."""
    assert "Net revenue" in _text(intc_10q_filing, "part_i_item_1")[:3000]


def test_intc_10k_risk_factors_is_the_body_section(intc_10k_filing):
    # FY2025 body heading is "| Risk Factors |"; FY2024 adds "and Other Key Information".
    text = _text(intc_10k_filing, "part_i_item_1a")
    assert text.startswith("| Risk Factors")
    assert len(text) > 20_000


def test_amd_10k_item_1a_and_8_start_at_their_headings(amd_10k_filing):
    assert _text(amd_10k_filing, "part_i_item_1a").lower().startswith(("# item 1a", "**item 1a"))
    assert _text(amd_10k_filing, "part_ii_item_8").startswith("# ITEM 8.")


def test_intc_10k_item_7_ends_before_item_8(intc_10k_filing):
    item7 = next(s for s in intc_10k_filing.sections if s.label == "part_ii_item_7")
    item8 = next(s for s in intc_10k_filing.sections if s.label == "part_ii_item_8")
    assert item7.char_end <= item8.char_start


# ── Helper units ──────────────────────────────────────────────────────────────

class TestFindTitleInBody:
    MD = (
        "| Risk Factors | 41 |\n| Controls | 41 |\n\n"
        + "x" * 200
        + "\n\n| Risk Factors and Other Key Information |  |\n"
        + "\n\n| Risk Factors and Other Key Information |\n\n# Risk Factors\n"
    )

    def test_skips_toc_rows_with_page_numbers_or_empty_cells(self):
        pos = _find_title_in_body(
            self.MD, "Risk Factors and Other Key Information", 0, len(self.MD)
        )
        assert pos == self.MD.rindex("| Risk Factors and Other Key Information |\n\n#")

    def test_no_body_heading_returns_none(self):
        assert _find_title_in_body("| Risk Factors | 41 |\n", "Risk Factors", 0, 30) is None


class TestSnapToTableStart:
    def test_mid_table_position_snaps_to_caption_row(self):
        md = (
            "prose\n\n| Statements of Operations |\n\n| (In Millions) | Q1 |\n"
            "| --- | --- |\n| Net revenue | 1 |\n| Basic | 2 |\n\nafter"
        )
        pos = md.index("Basic")
        assert md[_snap_to_table_start(md, pos):].startswith("| Statements of Operations |")

    def test_non_table_position_is_unchanged(self):
        md = "first paragraph\n\nsecond paragraph here"
        pos = md.index("second")
        assert _snap_to_table_start(md, pos) == pos


ALL_FIXTURES = [name for name, _ in FILINGS]


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_no_section_ends_with_a_dangling_heading_marker(request, fixture):
    """An anchor placed after '# ' used to leave the '#' at the end of the previous section."""
    filing = request.getfixturevalue(fixture)
    for sec in filing.sections:
        text = filing.text[sec.char_start:sec.char_end]
        assert not re.search(r"(?:^|\n)#+$", text), f"{fixture}/{sec.label} ends {text[-20:]!r}"


# ── Method labels say where the position really came from ────────────────────

def test_rejected_edgartools_anchor_is_labelled_heading(amd_10q_html, amd_10q_meta):
    """edgartools knows part_i_item_4 but its anchor lands in 'PART II'; the heading wins."""
    _, methods = parse_filing_with_methods(amd_10q_html, amd_10q_meta)
    assert methods["part_i_item_4"] == "heading"


def test_fallback_sections_exclude_heading_positioned_edgartools_keys(
    nvda_10k_html, nvda_10k_meta
):
    filing, methods = parse_filing_with_methods(nvda_10k_html, nvda_10k_meta)
    assert filing.fallback_sections == []
    assert "heading" in methods.values()  # honestly labelled, yet not a fallback


# ── Helper units (synthetic markdown, negative cases included) ───────────────

class TestFindStatementsCaption:
    def test_table_row_caption_matches(self):
        md = "intro\n\n| Consolidated Condensed Statements of Operations |\n\n| a | b |\n"
        assert _find_statements_caption(md, 0, len(md)) == md.index("| Consolidated")

    def test_prose_sentence_does_not_match(self):
        md = "Consolidated statements of income are included in Item 8.\n"
        assert _find_statements_caption(md, 0, len(md)) is None

    def test_toc_row_with_page_number_does_not_match(self):
        md = "| Consolidated Statements of Operations | 57 |\n"
        assert _find_statements_caption(md, 0, len(md)) is None


class TestHeadingSkeleton:
    def test_repeated_item_number_lands_on_the_next_heading(self):
        order = ["part_i_item_1", "part_i_item_2", "part_ii_item_1"]
        md = (
            "# Item 1. Financial Statements\n\nx\n\n# Item 2. MD&A\n\ny\n\n"
            "# Item 1. Legal Proceedings\n\nz\n"
        )
        found = _heading_skeleton(md, order, 0, len(md))
        assert found["part_ii_item_1"] == md.rindex("# Item 1.")

    def test_hit_in_the_index_tail_does_not_advance_the_floor(self):
        # part_i_item_3's only match is a two-cell index row in the tail; it must
        # not push the floor past the heading of the key that follows it.
        order = ["part_i_item_1", "part_i_item_3", "part_i_item_4"]
        md = (
            "# Item 1. Business\n\nbody\n\n# Item 4. Mine Safety\n\nmore\n\n"
            + "pad " * 2000
            + "\n| Item 3. | Legal Proceedings |\n"
        )
        found = _heading_skeleton(md, order, 0, len(md))
        assert found["part_i_item_4"] == md.index("# Item 4.")


class TestAnchorAgrees:
    def test_agrees_within_tolerance_of_reference(self):
        assert _anchor_agrees("x" * 1000, 500, 600)

    def test_disagrees_beyond_tolerance(self):
        assert not _anchor_agrees("x" * 5000, 100, 2000)

    def test_without_reference_mid_sentence_is_rejected(self):
        md = "some words before Financial Statements and more\n"
        assert not _anchor_agrees(md, md.index("Financial"), None)

    def test_without_reference_heading_line_is_accepted(self):
        md = "text\n\n# Legal Proceedings\n\nbody\n"
        assert _anchor_agrees(md, md.index("Legal"), None)

    def test_without_reference_page_number_row_is_rejected(self):
        md = "| Controls and Procedures | 102 |\n"
        assert not _anchor_agrees(md, md.index("Controls"), None)


class TestPageNumberRow:
    def test_page_number_cell(self):
        assert _is_page_number_row("| Risk Factors | 41 |\n", 3)

    def test_page_header_cell(self):
        assert _is_page_number_row("| Index to Financial Statements |  | Page |\n", 3)

    def test_ordinary_row(self):
        assert not _is_page_number_row("| Net revenue | 16,128 |\n", 3)


class TestSnapAnchor:
    def test_prefix_markup_is_pulled_into_the_section(self):
        md = "end of previous\n\n# Legal Proceedings\n\nbody"
        assert _snap_anchor(md, md.index("Legal")) == md.index("# Legal")

    def test_mid_sentence_position_is_unchanged(self):
        md = "see Legal Proceedings below"
        assert _snap_anchor(md, md.index("Legal")) == md.index("Legal")


class TestBareLineTier:
    def test_bare_title_line_found_when_no_row_or_heading(self):
        md = "intro text here\n\nFinancial Statements and Supplemental Details\n\nbody"
        pos = _find_title_in_body(md, "Financial Statements and Supplementary Data", 0, len(md))
        assert pos == md.index("Financial Statements and Supp")

    def test_one_cell_row_beats_an_earlier_bare_line(self):
        md = "Risk Factors\n\n| Risk Factors |\n\nbody"
        assert _find_title_in_body(md, "Risk Factors", 0, len(md)) == md.index("| Risk")
