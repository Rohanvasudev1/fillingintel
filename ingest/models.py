"""Pydantic models for the ingest pipeline."""
from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class FilingMeta(BaseModel):
    """Metadata for a single SEC filing, derived from the EDGAR submissions API."""

    model_config = {"frozen": True}

    cik: str
    accession_no: str  # "XXXXXXXXXX-YY-ZZZZZZ"
    form_type: str  # "10-K" or "10-Q"
    company_name: str
    fiscal_period: str  # "FY2025" or "FY2025-Q3"
    report_date: date
    filing_date: date
    primary_document: str  # filename only, e.g. "nvda-20250126.htm"

    @field_validator("accession_no")
    @classmethod
    def validate_accession_no(cls, v: str) -> str:
        if not _ACCESSION_RE.fullmatch(v):
            raise ValueError(
                f"accession_no must match XXXXXXXXXX-YY-ZZZZZZ, got {v!r}"
            )
        return v

    @field_validator("primary_document")
    @classmethod
    def validate_primary_document(cls, v: str) -> str:
        """Reject paths that could escape the cache directory."""
        if not _SAFE_FILENAME_RE.fullmatch(v):
            raise ValueError(
                f"primary_document must be a plain filename (no slashes or dots-dot), "
                f"got {v!r}"
            )
        return v


class ParsedSection(BaseModel):
    """A labelled section within a parsed filing, with character offsets into the text."""

    model_config = {"frozen": True}

    label: str  # e.g. "Item 1A", "Part I Item 2"
    char_start: Annotated[int, Field(ge=0)]
    char_end: int

    @field_validator("char_end")
    @classmethod
    def end_after_start(cls, v: int, info: ValidationInfo) -> int:
        char_start = info.data.get("char_start")
        if char_start is not None and v <= char_start:
            raise ValueError(
                f"char_end ({v}) must be strictly greater than char_start ({char_start})"
            )
        return v


class ParsedTable(BaseModel):
    """A table edgartools found in a filing's HTML, serialised as pipe-delimited markdown.

    Used only to count tables in the corpus report.  Table positions come from
    the parsed text (``ingest.tables``), not from this list.
    """

    model_config = {"frozen": True}

    index: int  # position in edgartools doc.tables
    markdown: str  # pipe-delimited row representation


class ParsedFiling(BaseModel):
    """Full parsed text of a filing with section boundaries as character offsets.

    ``text[section.char_start : section.char_end]`` must reproduce the exact
    source passage — this is what ``resolve(chunk_id)`` relies on.
    """

    model_config = {"frozen": True}

    accession_no: str
    cik: str
    form_type: str
    fiscal_period: str
    text: str
    sections: list[ParsedSection]
    tables: list[ParsedTable] = []
    missing_sections: list[str] = []
    fallback_sections: list[str] = []  # keys found via heading/cross_ref, not edgartools

    @model_validator(mode="after")
    def offsets_within_text(self) -> ParsedFiling:
        text_len = len(self.text)
        for section in self.sections:
            if section.char_end > text_len:
                raise ValueError(
                    f"Section '{section.label}' char_end {section.char_end} "
                    f"exceeds text length {text_len}"
                )
        return self
