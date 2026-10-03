"""Tests for ingest/models.py — run before implementation (RED phase)."""
import pytest
from pydantic import ValidationError

from ingest.models import FilingMeta, ParsedFiling, ParsedSection, ParsedTable


class TestParsedSection:
    def test_valid_section(self):
        s = ParsedSection(label="Item 1A", char_start=100, char_end=500)
        assert s.label == "Item 1A"
        assert s.char_start == 100
        assert s.char_end == 500

    def test_rejects_end_equal_to_start(self):
        with pytest.raises(ValidationError, match="char_end"):
            ParsedSection(label="Item 1A", char_start=100, char_end=100)

    def test_rejects_end_before_start(self):
        with pytest.raises(ValidationError, match="char_end"):
            ParsedSection(label="Item 1A", char_start=200, char_end=50)

    def test_immutable(self):
        s = ParsedSection(label="Item 1A", char_start=0, char_end=10)
        with pytest.raises(Exception):
            s.label = "changed"  # type: ignore[misc]


class TestParsedFiling:
    def test_valid_filing(self):
        text = "x" * 1000
        f = ParsedFiling(
            accession_no="0001045810-25-000003",
            cik="1045810",
            form_type="10-K",
            fiscal_period="FY2025",
            text=text,
            sections=[ParsedSection(label="Item 1A", char_start=0, char_end=200)],
        )
        assert f.fiscal_period == "FY2025"
        assert len(f.sections) == 1

    def test_rejects_section_end_beyond_text(self):
        with pytest.raises(ValidationError, match="char_end"):
            ParsedFiling(
                accession_no="0001045810-25-000003",
                cik="1045810",
                form_type="10-K",
                fiscal_period="FY2025",
                text="short",
                sections=[ParsedSection(label="Item 1A", char_start=0, char_end=1000)],
            )

    def test_empty_sections_valid(self):
        f = ParsedFiling(
            accession_no="0001045810-25-000003",
            cik="1045810",
            form_type="10-K",
            fiscal_period="FY2025",
            text="some text",
            sections=[],
        )
        assert f.sections == []

    def test_immutable(self):
        f = ParsedFiling(
            accession_no="0001045810-25-000003",
            cik="1045810",
            form_type="10-K",
            fiscal_period="FY2025",
            text="hello",
            sections=[],
        )
        with pytest.raises(Exception):
            f.text = "modified"  # type: ignore[misc]


class TestFilingMeta:
    # ── accession_no validator ────────────────────────────────────────────

    @pytest.mark.parametrize(
        "bad_accession",
        [
            "../../etc/passwd",          # path traversal
            "0001045810-25-00000",       # too short (5 digits in last segment)
            "0001045810-25-0000003",     # too long (7 digits in last segment)
            "000104581025000003",        # missing hyphens
            "XXXXXXXXXX-25-000003",      # letters in CIK segment
            "",                          # empty
        ],
    )
    def test_invalid_accession_no_rejected(self, bad_accession):
        from datetime import date

        with pytest.raises(Exception):  # ValidationError
            FilingMeta(
                cik="1045810",
                accession_no=bad_accession,
                form_type="10-K",
                company_name="NVIDIA CORP",
                fiscal_period="FY2025",
                report_date=date(2025, 1, 26),
                filing_date=date(2025, 2, 26),
                primary_document="nvda-20250126.htm",
            )

    # ── primary_document validator ────────────────────────────────────────

    @pytest.mark.parametrize(
        "bad_doc",
        [
            "../../etc/passwd",          # path traversal
            "subdir/file.htm",           # slash present
            "../sibling.htm",            # parent-dir traversal
            "file name.htm",             # space in filename
            "",                          # empty
        ],
    )
    def test_invalid_primary_document_rejected(self, bad_doc):
        from datetime import date

        with pytest.raises(Exception):  # ValidationError
            FilingMeta(
                cik="1045810",
                accession_no="0001045810-25-000003",
                form_type="10-K",
                company_name="NVIDIA CORP",
                fiscal_period="FY2025",
                report_date=date(2025, 1, 26),
                filing_date=date(2025, 2, 26),
                primary_document=bad_doc,
            )

    def test_valid_filing_meta(self):
        from datetime import date

        m = FilingMeta(
            cik="1045810",
            accession_no="0001045810-25-000003",
            form_type="10-K",
            company_name="NVIDIA CORP",
            fiscal_period="FY2025",
            report_date=date(2025, 1, 26),
            filing_date=date(2025, 2, 26),
            primary_document="nvda-20250126.htm",
        )
        assert m.cik == "1045810"
        assert m.fiscal_period == "FY2025"

    def test_immutable(self):
        from datetime import date

        m = FilingMeta(
            cik="1045810",
            accession_no="0001045810-25-000003",
            form_type="10-K",
            company_name="NVIDIA CORP",
            fiscal_period="FY2025",
            report_date=date(2025, 1, 26),
            filing_date=date(2025, 2, 26),
            primary_document="nvda-20250126.htm",
        )
        with pytest.raises(Exception):
            m.cik = "9999999"  # type: ignore[misc]


class TestParsedTable:
    def test_valid_table(self):
        t = ParsedTable(index=0, markdown="| a | b |\n| 1 | 2 |")
        assert t.index == 0
        assert t.markdown == "| a | b |\n| 1 | 2 |"

    def test_has_no_section_key(self):
        """Tables are placed by position in the text (Step 3a), not by a key on ParsedTable."""
        assert "section_key" not in ParsedTable.model_fields

    def test_immutable(self):
        t = ParsedTable(index=0, markdown="| a |")
        with pytest.raises(Exception):
            t.markdown = "changed"  # type: ignore[misc]


class TestParsedFilingExtended:
    """Tests for tables and missing_sections fields added in Step 2b."""

    def test_tables_default_empty(self):
        f = ParsedFiling(
            accession_no="0001045810-25-000003",
            cik="1045810",
            form_type="10-K",
            fiscal_period="FY2025",
            text="hello",
            sections=[],
        )
        assert f.tables == []

    def test_missing_sections_default_empty(self):
        f = ParsedFiling(
            accession_no="0001045810-25-000003",
            cik="1045810",
            form_type="10-K",
            fiscal_period="FY2025",
            text="hello",
            sections=[],
        )
        assert f.missing_sections == []

    def test_tables_stored(self):
        t = ParsedTable(index=0, markdown="| col |\n| val |")
        f = ParsedFiling(
            accession_no="0001045810-25-000003",
            cik="1045810",
            form_type="10-K",
            fiscal_period="FY2025",
            text="hello",
            sections=[],
            tables=[t],
        )
        assert len(f.tables) == 1
        assert f.tables[0].index == 0

    def test_missing_sections_stored(self):
        f = ParsedFiling(
            accession_no="0001045810-25-000003",
            cik="1045810",
            form_type="10-K",
            fiscal_period="FY2025",
            text="hello",
            sections=[],
            missing_sections=["part_ii_item_7", "part_ii_item_8"],
        )
        assert "part_ii_item_7" in f.missing_sections
