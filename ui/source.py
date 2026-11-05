"""A cited chunk's source passage: the chunk inside its filing text (Step 14).

The chunk is ``resolve()``'s text, the filing text between its offsets, with
up to ``CONTEXT_CHARS`` of the filing on each side.  Postgres cuts all three
pieces, so the whole filing never crosses the connection.  The EDGAR link is
built from the CIK and accession number; nothing is fetched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import LiteralString

import psycopg

from ingest.corpus import TICKER_BY_CIK
from ui.errors import SourceNotFound

CONTEXT_CHARS = 1500
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
# substr() is 1-based and counts characters, as Python slices do; resolve() reads the
# chunk the same way.
_SOURCE: LiteralString = (
    "SELECT c.accession_no, f.cik, f.company_name, f.form_type, f.fiscal_period, c.section, "
    "c.char_start, c.char_end, greatest(c.char_start - %(context)s, 0) AS context_start, "
    "substr(f.parsed_text, greatest(c.char_start - %(context)s, 0) + 1, "
    "       c.char_start - greatest(c.char_start - %(context)s, 0)), "
    "substr(f.parsed_text, c.char_start + 1, c.char_end - c.char_start), "
    "substr(f.parsed_text, c.char_end + 1, %(context)s) "
    "FROM chunks c JOIN filings f ON f.accession_no = c.accession_no "
    "WHERE c.chunk_id = %(chunk_id)s"
)


@dataclass(frozen=True)
class SourcePassage:
    chunk_id: str
    accession_no: str
    cik: str
    ticker: str
    company_name: str
    form_type: str
    fiscal_period: str
    section: str
    char_start: int
    char_end: int
    context_start: int  # where ``before`` starts in the filing text
    before: str
    chunk: str  # equal to resolve(chunk_id)
    after: str

    @property
    def edgar_url(self) -> str:
        return edgar_index_url(self.cik, self.accession_no)


def edgar_index_url(cik: str, accession_no: str) -> str:
    """The filing's index page on EDGAR."""
    return f"{EDGAR_ARCHIVES}/{int(cik)}/{accession_no.replace('-', '')}/{accession_no}-index.htm"


def read_source(conn: psycopg.Connection, chunk_id: str,
                context: int = CONTEXT_CHARS) -> SourcePassage:
    """*chunk_id*'s passage; ``SourceNotFound`` when no stored chunk has that ID."""
    row = conn.execute(_SOURCE, {"chunk_id": chunk_id, "context": context}).fetchone()
    if row is None:
        raise SourceNotFound(f"no stored chunk {chunk_id}")
    (accession_no, cik, company_name, form_type, fiscal_period, section, char_start, char_end,
     context_start, before, chunk, after) = row
    return SourcePassage(
        chunk_id=chunk_id, accession_no=accession_no, cik=cik,
        ticker=TICKER_BY_CIK.get(cik, cik), company_name=company_name, form_type=form_type,
        fiscal_period=fiscal_period, section=section, char_start=char_start,
        char_end=char_end, context_start=context_start, before=before, chunk=chunk,
        after=after)
