"""Postgres store for filings and chunks, and ``resolve(chunk_id)`` (Step 3c).

All functions take an open psycopg 3 connection; the caller owns it.  Every
query is parameterized.  ``load_filing`` commits: one filing loads atomically,
and later rollbacks on the same connection cannot undo it.
"""
from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import LiteralString, NamedTuple, cast

import psycopg

from ingest.chunker import Chunk, make_chunk_id
from ingest.models import ParsedFiling
from ingest.parsed_files import ParsedRecord, text_sha256
from ingest.parser import MIN_SECTION_CHARS

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
_POINTER_PHRASE = "set forth"  # NVIDIA's Item 8: statements "set forth" in Item 15
_REQUIRED_ENCODING = "UTF8"  # Python code-point offsets equal Postgres text positions only in UTF-8

_FILING_COLUMNS = (
    "accession_no", "cik", "company_name", "form_type", "fiscal_period", "report_date",
    "filing_date", "primary_document", "parsed_text", "text_sha256",
    "financial_statements_section", "parser_commit", "loaded_at",
)
_CHUNK_COLUMNS = (
    "chunk_id", "accession_no", "cik", "form_type", "fiscal_period", "section",
    "char_start", "char_end", "ordinal", "token_count", "tokenizer", "chunker_version",
    "contains_table",
)
_EMBEDDING_COLUMNS = (
    "chunk_id", "model", "dimensions", "text_sha256", "api_token_count", "embedding",
    "embedded_at",
)
_EXPECTED_COLUMNS = MappingProxyType({
    "filings": _FILING_COLUMNS, "chunks": _CHUNK_COLUMNS, "chunk_embeddings": _EMBEDDING_COLUMNS,
})
_FILING_FIELDS_ON_CHUNKS = ("cik", "form_type", "fiscal_period")

_UPSERT_FILING: LiteralString = """
INSERT INTO filings (
    accession_no, cik, company_name, form_type, fiscal_period, report_date, filing_date,
    primary_document, parsed_text, text_sha256, financial_statements_section, parser_commit
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (accession_no) DO UPDATE SET
    cik = EXCLUDED.cik,
    company_name = EXCLUDED.company_name,
    form_type = EXCLUDED.form_type,
    fiscal_period = EXCLUDED.fiscal_period,
    report_date = EXCLUDED.report_date,
    filing_date = EXCLUDED.filing_date,
    primary_document = EXCLUDED.primary_document,
    parsed_text = EXCLUDED.parsed_text,
    text_sha256 = EXCLUDED.text_sha256,
    financial_statements_section = EXCLUDED.financial_statements_section,
    parser_commit = EXCLUDED.parser_commit,
    loaded_at = now()
"""
# Built from the column tuple above, never from input.
_INSERT_CHUNK = cast(
    LiteralString,
    f"INSERT INTO chunks ({', '.join(_CHUNK_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(_CHUNK_COLUMNS))})",
)
_SELECT_CHUNK = cast(
    LiteralString, f"SELECT {', '.join(_CHUNK_COLUMNS)} FROM chunks WHERE chunk_id = %s"
)
_CHUNK_TEXT = "substr(f.parsed_text, c.char_start + 1, c.char_end - c.char_start)"
_RESOLVE = cast(
    LiteralString,
    f"SELECT {_CHUNK_TEXT} FROM chunks c JOIN filings f ON f.accession_no = c.accession_no"
    " WHERE c.chunk_id = %s",
)
_STORED_OFFSETS: LiteralString = (
    "SELECT chunk_id, char_start, char_end FROM chunks WHERE accession_no = %s ORDER BY ordinal"
)
_CHUNKS_FOR_EMBEDDING = cast(
    LiteralString,
    f"SELECT c.chunk_id, c.token_count, {_CHUNK_TEXT}, e.text_sha256"
    " FROM chunks c JOIN filings f ON f.accession_no = c.accession_no"
    " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = %s"
    " ORDER BY c.chunk_id",
)
_INSERTED_EMBEDDING_COLUMNS = _EMBEDDING_COLUMNS[:-1]  # embedded_at is set by the database
# Built from the column tuple above, never from input.  The last value is cast to vector.
_UPSERT_EMBEDDING = cast(
    LiteralString,
    f"INSERT INTO chunk_embeddings ({', '.join(_INSERTED_EMBEDDING_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * (len(_INSERTED_EMBEDDING_COLUMNS) - 1))}, %s::vector) "
    "ON CONFLICT (chunk_id, model) DO UPDATE SET "
    + ", ".join(f"{col} = EXCLUDED.{col}" for col in _INSERTED_EMBEDDING_COLUMNS[2:])
    + ", embedded_at = now()",
)
# The query vector is bound once and cast to vector; exact search, no index (ADR-0002).
_NEAREST: LiteralString = """
WITH q AS (SELECT %s::vector AS v)
SELECT e.chunk_id, 1 - (e.embedding <=> q.v) AS score
FROM chunk_embeddings e, q
WHERE e.model = %s
ORDER BY e.embedding <=> q.v, e.chunk_id
LIMIT %s
"""
_EMBEDDING_GAPS = cast(
    LiteralString,
    "SELECT count(*) FILTER (WHERE e.chunk_id IS NULL),"
    " count(*) FILTER (WHERE e.chunk_id IS NOT NULL AND e.text_sha256 <>"
    f" encode(sha256(convert_to({_CHUNK_TEXT}, 'UTF8')), 'hex'))"
    " FROM chunks c JOIN filings f ON f.accession_no = c.accession_no"
    " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = %s",
)
_CHUNKER_VERSIONS: LiteralString = "SELECT DISTINCT chunker_version FROM chunks ORDER BY 1"
_LIVE_COLUMNS: LiteralString = """
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name IN ('filings', 'chunks', 'chunk_embeddings')
"""


class ChunkForEmbedding(NamedTuple):
    """A chunk's text and cl100k token count, with the text hash stored for one model."""

    chunk_id: str
    token_count: int
    text: str
    stored_sha256: str | None  # None when the chunk has no vector for the model


class EmbeddingGaps(NamedTuple):
    """Chunks whose *model* vector is absent, or was made from different text."""

    missing: int
    stale: int


class ChunkNotFound(KeyError):
    """No chunk with this ID is stored."""


class SchemaMismatch(RuntimeError):
    """The live database does not match what this code expects."""


# ── Schema ────────────────────────────────────────────────────────────────────

def _check_live_schema(conn: psycopg.Connection) -> None:
    encoding = conn.execute("SHOW server_encoding").fetchone()
    if encoding is None or encoding[0] != _REQUIRED_ENCODING:
        raise SchemaMismatch(f"server_encoding is {encoding}, expected {_REQUIRED_ENCODING}")
    live: dict[str, set[str]] = {table: set() for table in _EXPECTED_COLUMNS}
    for table, column in conn.execute(_LIVE_COLUMNS).fetchall():
        live[table].add(column)
    for table, expected in _EXPECTED_COLUMNS.items():
        extra, missing = live[table] - set(expected), set(expected) - live[table]
        if extra or missing:
            raise SchemaMismatch(
                f"{table} columns differ from the code: extra {sorted(extra)}, "
                f"missing {sorted(missing)}; migrate or recreate the table"
            )


def apply_schema(conn: psycopg.Connection) -> None:
    """Create the tables if absent, check the live columns match the code, and commit.

    ``CREATE TABLE IF NOT EXISTS`` skips a table that already exists, so a later
    change to ``db/schema.sql`` would otherwise go unnoticed; the column check
    raises ``SchemaMismatch`` instead.
    """
    conn.execute(cast(LiteralString, SCHEMA_PATH.read_text(encoding="utf-8")))
    _check_live_schema(conn)
    conn.commit()


# ── Loading ───────────────────────────────────────────────────────────────────

def financial_statements_section(filing: ParsedFiling) -> str:
    """The section that holds a filing's financial statements.

    A 10-Q's are in Part I Item 1.  A 10-K's are in Item 8, unless Item 8 is
    the short verified pointer to Item 15 (NVIDIA), when they are in Item 15.
    """
    if filing.form_type != "10-K":
        return "part_i_item_1"
    item_8 = "".join(
        filing.text[s.char_start : s.char_end]
        for s in filing.sections
        if s.label == "part_ii_item_8"
    )
    has_item_15 = any(s.label == "part_iv_item_15" for s in filing.sections)
    is_pointer = len(item_8) < MIN_SECTION_CHARS and _POINTER_PHRASE in item_8.lower()
    return "part_iv_item_15" if is_pointer and has_item_15 else "part_ii_item_8"


def _check_chunks(record: ParsedRecord, chunks: tuple[Chunk, ...]) -> None:
    """Refuse chunks that do not belong to *record* or point outside its text."""
    meta, text_len = record.meta, len(record.filing.text)
    for c in chunks:
        if c.accession_no != meta.accession_no:
            raise ValueError(f"chunk {c.chunk_id} is from another filing than {meta.accession_no}")
        if c.chunk_id != make_chunk_id(meta.accession_no, c.ordinal):
            raise ValueError(f"chunk_id {c.chunk_id} does not match ordinal {c.ordinal}")
        if c.char_end > text_len:
            raise ValueError(f"chunk {c.chunk_id} ends past the end of the text ({text_len})")
        for field in _FILING_FIELDS_ON_CHUNKS:
            if getattr(c, field) != getattr(meta, field):
                raise ValueError(f"chunk {c.chunk_id} {field} differs from its filing")


def _filing_row(record: ParsedRecord) -> tuple[object, ...]:
    meta, filing = record.meta, record.filing
    return (
        meta.accession_no, meta.cik, meta.company_name, meta.form_type, meta.fiscal_period,
        meta.report_date, meta.filing_date, meta.primary_document, filing.text,
        text_sha256(filing.text),
        financial_statements_section(filing), record.parser_commit,
    )


def load_filing(
    conn: psycopg.Connection, record: ParsedRecord, chunks: tuple[Chunk, ...]
) -> None:
    """Upsert one filing and replace its chunks atomically, then commit.

    Raises ``ValueError`` before touching the database if a chunk belongs to
    another filing, has an ID that disagrees with its ordinal, ends past the
    text, or disagrees with the filing's cik, form type or period.  If the
    caller has an open transaction, it is committed too.
    """
    _check_chunks(record, chunks)
    with conn.transaction():
        conn.execute(_UPSERT_FILING, _filing_row(record))
        conn.execute("DELETE FROM chunks WHERE accession_no = %s", (record.meta.accession_no,))
        with conn.cursor() as cur:
            cur.executemany(
                _INSERT_CHUNK, [tuple(getattr(c, col) for col in _CHUNK_COLUMNS) for c in chunks]
            )
    conn.commit()


# ── Reading ───────────────────────────────────────────────────────────────────

def get_chunk(conn: psycopg.Connection, chunk_id: str) -> Chunk:
    """The stored chunk *chunk_id*; raises ``ChunkNotFound``."""
    row = conn.execute(_SELECT_CHUNK, (chunk_id,)).fetchone()
    if row is None:
        raise ChunkNotFound(chunk_id)
    return Chunk(**dict(zip(_CHUNK_COLUMNS, row, strict=True)))


def resolve(conn: psycopg.Connection, chunk_id: str) -> str:
    """The exact source text of *chunk_id*: its offsets applied to the filing text."""
    row = conn.execute(_RESOLVE, (chunk_id,)).fetchone()
    if row is None:
        raise ChunkNotFound(chunk_id)
    return row[0]


def stored_offsets(conn: psycopg.Connection, accession_no: str) -> list[tuple[str, int, int]]:
    """``(chunk_id, char_start, char_end)`` for each stored chunk of one filing, in order.

    An unknown accession gives ``[]``; callers compare against an expected list.
    """
    return conn.execute(_STORED_OFFSETS, (accession_no,)).fetchall()


# ── Embeddings ────────────────────────────────────────────────────────────────

def chunks_for_embedding(conn: psycopg.Connection, model: str) -> list[ChunkForEmbedding]:
    """Every stored chunk, in ID order, with the text hash of its *model* vector if any."""
    return [ChunkForEmbedding(*row) for row in conn.execute(_CHUNKS_FOR_EMBEDDING, (model,))]


def save_embedding(
    conn: psycopg.Connection,
    chunk_id: str,
    model: str,
    text: str,
    vector: tuple[float, ...],
    api_token_count: int,
) -> None:
    """Insert or replace the *model* vector of one chunk, then commit.

    The vector goes in as pgvector's text form, so no pgvector package is needed.
    """
    conn.execute(_UPSERT_EMBEDDING, (
        chunk_id, model, len(vector), text_sha256(text), api_token_count, _vector_literal(vector),
    ))
    conn.commit()


def _vector_literal(vector: tuple[float, ...]) -> str:
    return "[" + ",".join(repr(x) for x in vector) + "]"


def embedding_gaps(conn: psycopg.Connection, model: str) -> EmbeddingGaps:
    """How many chunks lack a current *model* vector; the hash is recomputed in Postgres."""
    return EmbeddingGaps(*conn.execute(_EMBEDDING_GAPS, (model,)).fetchone())


def chunker_versions(conn: psycopg.Connection) -> list[str]:
    """The distinct ``chunker_version`` values of the stored chunks, sorted."""
    return [row[0] for row in conn.execute(_CHUNKER_VERSIONS)]


def nearest_chunks(
    conn: psycopg.Connection, model: str, vector: tuple[float, ...], k: int
) -> list[tuple[str, float]]:
    """The *k* chunks whose *model* vectors are most cosine-similar to *vector*.

    Exact search, best first, ties broken by chunk ID.  Each pair is
    ``(chunk_id, cosine similarity)``.
    """
    return conn.execute(_NEAREST, (_vector_literal(vector), model, k)).fetchall()
