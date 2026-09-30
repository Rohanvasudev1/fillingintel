"""Tests for ingest/models.py — run before implementation (RED phase)."""
import pytest
from pydantic import ValidationError

from ingest.models import FilingMeta, ParsedFiling, ParsedSection


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
