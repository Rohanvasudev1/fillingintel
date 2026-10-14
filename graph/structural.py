"""The structural rows the graph builder needs, read from Postgres (Step 10; Step 11).

Three fixed SELECTs; columns are read by name. The risk-factor section of each
filing (RISK_SECTION_BY_FORM, passed as parameters) runs from its first
chunk's start to its last chunk's end; a filing whose form has no such section,
or that has no chunk in it, gets none.
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from graph.build_input import (
    RISK_SECTION_BY_FORM,
    ChunkRow,
    FilingRow,
    RiskSection,
    StructuralRows,
)

_RISK_SECTIONS = """
    SELECT f.accession_no, s.char_start,
           substr(f.parsed_text, s.char_start + 1, s.char_end - s.char_start) AS text
    FROM filings f
    JOIN (SELECT c.accession_no, min(c.char_start) AS char_start, max(c.char_end) AS char_end
          FROM chunks c
          WHERE (c.form_type = '10-K' AND c.section = %(ten_k)s)
             OR (c.form_type = '10-Q' AND c.section = %(ten_q)s)
          GROUP BY c.accession_no) s USING (accession_no)
    ORDER BY f.accession_no
"""


def read_structural(conn: psycopg.Connection) -> StructuralRows:
    """Every filing, chunk and risk-factor section, in key order, read in one transaction."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        filings = cur.execute(
            "SELECT accession_no, cik, company_name, form_type, fiscal_period, filing_date "
            "FROM filings ORDER BY accession_no"
        ).fetchall()
        chunks = cur.execute(
            "SELECT chunk_id, accession_no, section, char_start, char_end "
            "FROM chunks ORDER BY chunk_id"
        ).fetchall()
        sections = cur.execute(
            _RISK_SECTIONS,
            {"ten_k": RISK_SECTION_BY_FORM["10-K"], "ten_q": RISK_SECTION_BY_FORM["10-Q"]},
        ).fetchall()
    return StructuralRows(tuple(FilingRow(**row) for row in filings),
                          tuple(ChunkRow(**row) for row in chunks),
                          tuple(RiskSection(**row) for row in sections))
