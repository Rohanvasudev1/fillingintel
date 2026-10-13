"""Postgres reads shared by the persistence commands (Step 10)."""
from __future__ import annotations

from collections.abc import Sequence

import psycopg

from ingest.store import list_filings, resolve
from persist.filings import Filing


def postgres_filings(url: str) -> list[Filing]:
    """Every loaded filing, as filing pairing needs it."""
    with psycopg.connect(url) as conn:
        return [Filing(r.accession_no, r.cik, r.form_type, r.report_date)
                for r in list_filings(conn)]


def postgres_texts(url: str, chunk_ids: Sequence[str]) -> dict[str, str]:
    """The text resolve() returns for each chunk, from one connection."""
    with psycopg.connect(url) as conn:
        return {chunk_id: resolve(conn, chunk_id) for chunk_id in chunk_ids}
