"""Whole-filing coverage (preamble) and sections that continue after another item.

Two defects found after the first full-corpus run:

* Text before the first located section (cover page, forward-looking statements)
  was dropped from ``ParsedFiling.text``, so some of the filing had no offsets.
* Intel's 10-K prints "Critical Accounting Estimates" (Item 7 per its own
  cross-reference index, pages 34-36) after Item 7A, so a single contiguous
  Item 7 span stopped before it and left it inside the Item 7A section.
"""
import re

import pytest
from edgar.documents import parse_html

from ingest.parser import (
    PREAMBLE_KEY,
    _assemble_sections,
    _continuation_starts,
    _parse_index_subtitles,
)

FIXTURES = [
    "nvda_10k", "intc_10k", "amd_10k", "nvda_10q", "intc_10q", "amd_10q",
]

# The real last sentence of Intel's FY2025 MD&A (Critical Accounting Estimates,
# page 36), followed by the page footer that precedes the Risk Factors heading.
INTC_FY2025_MDNA_LAST = (
    "If one or more of these matters were resolved against us for amounts in "
    "excess of management's estimates of losses, our results of operations and "
    "financial condition could be materially adversely affected\\.\n\n| MD&A | 36 |"
)


def _spans(filing, key: str) -> list[str]:
    return [
        filing.text[s.char_start:s.char_end]
        for s in sorted(filing.sections, key=lambda s: s.char_start)
        if s.label == key
    ]


# ── Preamble: the whole filing is covered by offsets ─────────────────────────

@pytest.mark.parametrize("name", FIXTURES)
def test_sections_reproduce_the_whole_filing(request, name):
    html = request.getfixturevalue(f"{name}_html")
    filing = request.getfixturevalue(f"{name}_filing")
    md = parse_html(html).to_markdown()
    assert "".join(filing.text.split()) == "".join(md.split())


@pytest.mark.parametrize("name", FIXTURES)
def test_preamble_is_the_first_section(request, name):
    filing = request.getfixturevalue(f"{name}_filing")
    first = min(filing.sections, key=lambda s: s.char_start)
    assert (first.label, first.char_start) == (PREAMBLE_KEY, 0)
    assert "preamble" not in filing.fallback_sections
    assert "preamble" not in filing.missing_sections


@pytest.mark.parametrize("name", FIXTURES)
def test_preamble_holds_the_cover_page(request, name):
    filing = request.getfixturevalue(f"{name}_filing")
    preamble = _spans(filing, "preamble")[0]
    assert re.search(r"FORM\s+10-[KQ]", preamble, re.IGNORECASE)


# ── Continuation: Intel's Item 7 continues after Item 7A ─────────────────────

def test_intc_10k_item_7_has_a_second_span_ending_at_the_real_last_sentence(intc_10k_filing):
    spans = _spans(intc_10k_filing, "part_ii_item_7")
    assert len(spans) == 2
    assert spans[-1].endswith(INTC_FY2025_MDNA_LAST)


def test_intc_10k_item_7_second_span_starts_at_critical_accounting_estimates(intc_10k_filing):
    spans = _spans(intc_10k_filing, "part_ii_item_7")
    assert spans[-1].startswith("Critical Accounting Estimates")


def test_intc_10k_item_7a_no_longer_contains_critical_accounting_estimates(intc_10k_filing):
    (item_7a,) = _spans(intc_10k_filing, "part_ii_item_7a")
    assert item_7a.startswith("Quantitative and Qualitative Disclosures About Market Risk")
    assert "Critical Accounting Estimates" not in item_7a


def test_sections_still_do_not_overlap_with_continuations(intc_10k_filing):
    ordered = sorted(intc_10k_filing.sections, key=lambda s: s.char_start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.char_end <= b.char_start


def test_filings_without_an_index_have_single_span_item_7(nvda_10k_filing, amd_10k_filing):
    assert len(_spans(nvda_10k_filing, "part_ii_item_7")) == 1
    assert len(_spans(amd_10k_filing, "part_ii_item_7")) == 1


# ── Helper units ──────────────────────────────────────────────────────────────

class TestParseIndexSubtitles:
    INDEX = (
        "| Item Number | Item |  |\n"
        "| Item 7. | MD&A: |  |\n"
        "|  | Liquidity and capital resources | Pages 29-32 |\n"
        "|  | Critical accounting estimates | Pages 34-36, 65-72 |\n"
        "| Item 7A. | Market Risk | Pages 33 |\n"
        "| Item 8. | Financial Statements | Pages 56-108 |\n"
    )

    def test_sub_rows_attach_to_the_preceding_item(self):
        md = "x" * 2000 + "\n" + self.INDEX
        subs = _parse_index_subtitles(md)
        assert subs["7"] == ["Liquidity and capital resources", "Critical accounting estimates"]
        assert subs["7a"] == [] and subs["8"] == []

    def test_no_index_gives_nothing(self):
        assert _parse_index_subtitles("just prose\n" * 200) == {}


class TestContinuationStarts:
    def _md(self):
        return (
            "AAA\n\nMD&A main text\n\n"
            "Quantitative and Qualitative Disclosures\n\nrisk text\n\n"
            "Critical Accounting Estimates\n\nestimates text\n\n"
            "| Risk Factors |\n\nrisk factors text\n"
        )

    def _positions(self, md):
        return {
            "part_ii_item_7": (md.index("MD&A main"), "cross_reference_index"),
            "part_ii_item_7a": (md.index("Quantitative"), "edgartools"),
            "part_i_item_1a": (md.index("| Risk Factors |"), "cross_reference_index"),
        }

    def test_title_between_other_sections_and_next_required_becomes_a_span(self):
        md = self._md()
        out = _continuation_starts(
            md,
            self._positions(md),
            required=frozenset({"part_ii_item_7", "part_i_item_1a"}),
            subtitles={"7": ["Critical accounting estimates"]},
            max_pos=len(md),
        )
        assert out == [("part_ii_item_7", md.index("Critical Accounting"))]

    def test_title_inside_the_primary_span_is_not_a_continuation(self):
        md = self._md().replace(
            "MD&A main text", "MD&A main text\n\nLiquidity and capital resources"
        )
        out = _continuation_starts(
            md,
            self._positions(md),
            required=frozenset({"part_ii_item_7", "part_i_item_1a"}),
            subtitles={"7": ["Liquidity and capital resources"]},
            max_pos=len(md),
        )
        assert out == []

    def test_title_after_the_next_required_section_is_ignored(self):
        md = self._md() + "\nResults of operations\n\nlate text\n"
        out = _continuation_starts(
            md,
            self._positions(md),
            required=frozenset({"part_ii_item_7", "part_i_item_1a"}),
            subtitles={"7": ["Results of operations"]},
            max_pos=len(md),
        )
        assert out == []


    def test_no_later_required_section_means_no_search(self):
        # Item 8 is the last required section: nothing bounds a window after it.
        md = "# Item 8\n\nstatements\n\nResults of operations\n\nexhibit prose\n"
        out = _continuation_starts(
            md,
            {"part_ii_item_8": (0, "heading"), "part_ii_item_9": (md.index("Results"), "heading")},
            required=frozenset({"part_ii_item_8"}),
            subtitles={"8": ["Results of operations"]},
            max_pos=len(md),
        )
        assert out == []

    def test_generic_titles_are_ignored(self):
        md = self._md()
        out = _continuation_starts(
            md,
            self._positions(md),
            required=frozenset({"part_ii_item_7", "part_i_item_1a"}),
            subtitles={"7": ["None", "Not applicable"]},
            max_pos=len(md),
        )
        assert out == []

    def test_item_numbers_shared_across_parts_are_skipped(self):
        # 10-Q: part_i_item_1 and part_ii_item_1 share the index number "1".
        md = self._md()
        pos = {
            "part_i_item_1": (md.index("MD&A main"), "heading"),
            "part_ii_item_1": (md.index("Quantitative"), "heading"),
            "part_i_item_1a": (md.index("| Risk Factors |"), "heading"),
        }
        out = _continuation_starts(
            md, pos, frozenset({"part_i_item_1", "part_i_item_1a"}),
            {"1": ["Critical accounting estimates"]}, len(md),
        )
        assert out == []


class TestAssembleSections:
    def test_continuation_of_an_edgartools_key_is_not_a_fallback(self):
        md = "cover\n\nITEM7 main\n\nITEM7A risk\n\nCritical estimates\n\nend"
        found = [
            ("part_ii_item_7", md.index("ITEM7 main"), "edgartools"),
            ("part_ii_item_7a", md.index("ITEM7A"), "edgartools"),
            ("part_ii_item_7", md.index("Critical"), "continuation"),
        ]
        text, sections, fallback, methods = _assemble_sections(
            md, found, frozenset({"part_ii_item_7", "part_ii_item_7a"})
        )
        assert fallback == []
        assert methods["part_ii_item_7"] == "edgartools"
        assert [s.label for s in sections] == [
            PREAMBLE_KEY, "part_ii_item_7", "part_ii_item_7a", "part_ii_item_7",
        ]

    def test_no_located_sections_makes_the_whole_filing_the_preamble(self):
        md = "just some text with no items at all"
        text, sections, fallback, methods = _assemble_sections(md, [], frozenset())
        assert [s.label for s in sections] == [PREAMBLE_KEY]
        assert text == md

    def test_section_at_offset_zero_gets_no_preamble(self):
        md = "ITEM1 body text\n\nITEM2 more"
        found = [("part_i_item_1", 0, "heading"), ("part_i_item_2", md.index("ITEM2"), "heading")]
        _, sections, _, _ = _assemble_sections(md, found, frozenset())
        assert [s.label for s in sections] == ["part_i_item_1", "part_i_item_2"]

    def test_whitespace_only_text_before_the_first_section_is_skipped(self):
        md = "   \n\nITEM1 body"
        _, sections, _, _ = _assemble_sections(
            md, [("part_i_item_1", md.index("ITEM1"), "heading")], frozenset()
        )
        assert [s.label for s in sections] == ["part_i_item_1"]
