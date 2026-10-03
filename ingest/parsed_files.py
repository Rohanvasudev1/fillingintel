"""Parsed filings on disk: ``data/parsed/{accession_no}.json`` (Step 3c).

``python -m ingest.corpus`` writes one file per manifest filing (it needs the
network to pick the manifest).  ``python -m ingest.load`` reads them offline.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pydantic import BaseModel, model_validator

from ingest.models import FilingMeta, ParsedFiling
from ingest.provenance import REPO_ROOT

PARSED_SUFFIX = ".json"
DEFAULT_PARSED_DIR = REPO_ROOT / "data" / "parsed"


def text_sha256(text: str) -> str:
    """Hex SHA-256 of *text* as UTF-8; db/schema.sql checks filings.text_sha256 the same way."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ParsedRecord(BaseModel):
    """One filing's metadata and parse, plus the commit that produced the parse."""

    model_config = {"frozen": True}  # dict form, as in ingest/models.py

    meta: FilingMeta
    filing: ParsedFiling
    parser_commit: str

    @model_validator(mode="after")
    def meta_matches_filing(self) -> ParsedRecord:
        if self.meta.accession_no != self.filing.accession_no:
            raise ValueError(
                f"meta accession {self.meta.accession_no} != "
                f"filing accession {self.filing.accession_no}"
            )
        return self


def write_parsed(record: ParsedRecord, parsed_dir: Path) -> Path:
    """Write *record* to ``parsed_dir/{accession_no}.json`` atomically and return the path.

    The JSON goes to a temporary file first and is then renamed, so a crash
    never leaves a truncated file for the loader to trip on.
    """
    parsed_dir.mkdir(parents=True, exist_ok=True)
    path = parsed_dir / f"{record.meta.accession_no}{PARSED_SUFFIX}"
    tmp = path.with_suffix(f"{PARSED_SUFFIX}.tmp")
    tmp.write_text(record.model_dump_json(), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_parsed(path: Path) -> ParsedRecord:
    """Read one parsed file; its name must match the accession number inside it."""
    record = ParsedRecord.model_validate_json(path.read_text(encoding="utf-8"))
    if path.stem != record.meta.accession_no:
        raise ValueError(
            f"file name {path.name} does not match accession {record.meta.accession_no}"
        )
    return record


def read_parsed_dir(parsed_dir: Path) -> list[ParsedRecord]:
    """Every parsed file in *parsed_dir*, sorted by accession number."""
    if not parsed_dir.is_dir():
        raise FileNotFoundError(f"parsed directory not found: {parsed_dir}")
    return [read_parsed(p) for p in sorted(parsed_dir.glob(f"*{PARSED_SUFFIX}"))]
