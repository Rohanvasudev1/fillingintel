"""The structural rows the graph builder needs, read from Postgres (Step 10).

Two fixed SELECTs with no parameters; columns are read by name.
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from graph.build_input import ChunkRow, FilingRow, StructuralRows


def read_structural(conn: psycopg.Connection) -> StructuralRows:
    """Every filing and chunk, in key order, read in one transaction."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        filings = cur.execute(
            "SELECT accession_no, cik, company_name, form_type, fiscal_period, filing_date "
            "FROM filings ORDER BY accession_no"
        ).fetchall()
        chunks = cur.execute(
            "SELECT chunk_id, accession_no, section FROM chunks ORDER BY chunk_id"
        ).fetchall()
    return StructuralRows(tuple(FilingRow(**row) for row in filings),
                          tuple(ChunkRow(**row) for row in chunks))
