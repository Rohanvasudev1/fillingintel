"""The corpus snapshot the quality gate loads into an empty Postgres (Step 6, ADR-0003).

One gzipped JSON Lines file.  The first line is a header with the format, the
embedding model, the row counts and the SHA-256 of the lines after it.  Then
come the ``filings`` rows (with parsed text), the ``chunks`` rows, the
``chunk_embeddings`` rows for the model, and the query vectors of the gated
questions, keyed by model and the SHA-256 of the question text (the question
text itself is not stored).  Rows are in primary key order, keys are sorted and
gzip's timestamp is zero, so rebuilding from unchanged data gives the same bytes.

Built from the local database and query cache by ``scripts/build_fixtures.py``.
Loading checks gzip's CRC and the body hash, then inserts through the real
schema, so its CHECKs run on every row.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType

import psycopg

from eval.schema import NO_GOLD_CLASSES, EvalRecord
from ingest.parsed_files import text_sha256
from ingest.store import (
    SNAPSHOT_CHUNK_COLUMNS,
    SNAPSHOT_EMBEDDING_COLUMNS,
    SNAPSHOT_FILING_COLUMNS,
    SchemaMismatch,
    SnapshotRows,
    apply_schema,
    export_rows,
    import_rows,
    require_current_embeddings,
)
from ingest.voyage import MODELS, Embedding, VoyageError
from retrieve.query_cache import CachedQueryEmbedder, CacheError

SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "corpus_snapshot.jsonl.gz"
)
FORMAT = 1
GATED_SPLIT = "dev"
_QUERY_TABLE = "query_vectors"
_QUERY_COLUMNS = ("model", "text_sha256", "vector", "api_token_count")
_COLUMNS = MappingProxyType({
    "filings": frozenset(SNAPSHOT_FILING_COLUMNS),
    "chunks": frozenset(SNAPSHOT_CHUNK_COLUMNS),
    "chunk_embeddings": frozenset(SNAPSHOT_EMBEDDING_COLUMNS),
    _QUERY_TABLE: frozenset(_QUERY_COLUMNS),
})
_DATE_COLUMNS = ("report_date", "filing_date")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class SnapshotError(RuntimeError):
    """A snapshot cannot be built, read or loaded, or has no vector for a question."""


class MissingQueryVector(SnapshotError, CacheError):
    """The snapshot has no vector for a question; ``VectorArm`` reports it as an ``ArmError``."""


@dataclass(frozen=True)
class QueryVector:
    """A gated question's query embedding, keyed by model and question text hash."""

    model: str
    text_sha256: str
    vector: tuple[float, ...]
    api_token_count: int


@dataclass(frozen=True)
class Snapshot:
    """A snapshot file's contents, checked, and the SHA-256 of the file's bytes."""

    sha256: str  # of the gzipped file
    model: str
    rows: SnapshotRows
    query_vectors: tuple[QueryVector, ...]  # in (model, text_sha256) order


# ── Questions ─────────────────────────────────────────────────────────────────

def gated_records(records: Iterable[EvalRecord]) -> tuple[EvalRecord, ...]:
    """The answerable ``dev`` records, in file order: the questions the quality gate runs."""
    return tuple(r for r in records if r.split == GATED_SPLIT and r.class_ not in NO_GOLD_CLASSES)


def gated_questions(records: Iterable[EvalRecord]) -> tuple[str, ...]:
    """The text of each gated question, in file order."""
    return tuple(r.question for r in gated_records(records))


class _NoNetwork:
    """Stands in for Voyage behind the query cache, so a cache miss never calls the API."""

    def embed_query(self, text: str, model: str) -> Embedding:
        raise VoyageError("not in the query cache")


def query_vectors_from_cache(
    questions: Sequence[str], model: str, cache_dir: Path
) -> tuple[QueryVector, ...]:
    """Each question's cached query vector; raises ``SnapshotError`` if one is not cached."""
    cache = CachedQueryEmbedder(_NoNetwork(), cache_dir)
    return tuple(_cached_vector(cache, question, model) for question in questions)


def _cached_vector(cache: CachedQueryEmbedder, question: str, model: str) -> QueryVector:
    sha = text_sha256(question)
    try:
        embedding = cache.embed_query(question, model)
    except (VoyageError, CacheError) as exc:
        raise SnapshotError(f"question {sha[:12]} is not in the query cache: {exc}") from exc
    return QueryVector(model, sha, embedding.vector, embedding.api_token_count)


class SnapshotQueryEmbedder:
    """A ``QueryEmbedder`` serving the snapshot's vectors; never calls Voyage."""

    def __init__(self, query_vectors: Iterable[QueryVector]):
        self._vectors = {(v.model, v.text_sha256): v for v in query_vectors}

    def embed_query(self, text: str, model: str) -> Embedding:
        """The stored vector for *text* and *model*; raises ``MissingQueryVector`` for any other."""
        stored = self._vectors.get((model, text_sha256(text)))
        if stored is None:
            raise MissingQueryVector(
                f"no snapshot vector for question {text_sha256(text)[:12]} and model {model}"
            )
        return Embedding(stored.vector, stored.api_token_count, from_cache=True)


# ── Building ──────────────────────────────────────────────────────────────────

def build_snapshot(
    conn: psycopg.Connection, model: str, query_vectors: Sequence[QueryVector]
) -> bytes:
    """The snapshot of every stored filing, chunk and *model* vector, with *query_vectors*.

    Raises ``SnapshotError`` if a chunk lacks a current *model* vector.
    """
    try:
        require_current_embeddings(conn, model)
    except ValueError as exc:
        raise SnapshotError(f"{exc}; run ingest.embed first") from exc
    return encode_snapshot(export_rows(conn, model), model, query_vectors)


def encode_snapshot(
    rows: SnapshotRows, model: str, query_vectors: Sequence[QueryVector]
) -> bytes:
    """The gzipped snapshot bytes; the same input in any order gives the same bytes.

    Raises ``SnapshotError`` if two different query vectors share a model and text hash.
    """
    by_key = {(v.model, v.text_sha256): v for v in query_vectors}
    if len(by_key) != len(set(query_vectors)):
        raise SnapshotError("two different query vectors share a model and question hash")
    body_rows = [
        *(("filings", r) for r in sorted(rows.filings, key=lambda r: r["accession_no"])),
        *(("chunks", r) for r in sorted(rows.chunks, key=lambda r: r["chunk_id"])),
        *(("chunk_embeddings", r) for r in sorted(rows.embeddings, key=lambda r: r["chunk_id"])),
        *((_QUERY_TABLE, _query_row(by_key[key])) for key in sorted(by_key)),
    ]
    body = "".join(_line({"table": table, "row": row}) for table, row in body_rows)
    counts = {table: sum(1 for t, _ in body_rows if t == table) for table in _COLUMNS}
    header = {
        "format": FORMAT,
        "model": model,
        "counts": counts,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    return gzip.compress((_line(header) + body).encode("utf-8"), mtime=0)


def _query_row(v: QueryVector) -> dict[str, object]:
    return {
        "model": v.model, "text_sha256": v.text_sha256,
        "vector": list(v.vector), "api_token_count": v.api_token_count,
    }


def _line(obj: dict[str, object]) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_value
    ) + "\n"


def _json_value(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"cannot store {type(value).__name__} in a snapshot")


# ── Reading and loading ───────────────────────────────────────────────────────

def read_snapshot(path: Path) -> Snapshot:
    """Read and check a snapshot file without touching a database.

    Raises ``SnapshotError`` if the file is missing, damaged (gzip CRC), edited
    (body hash), or holds rows with unexpected columns or bad query vectors.
    """
    try:
        raw = path.read_bytes()
        text = gzip.decompress(raw).decode("utf-8")
    except (OSError, EOFError, zlib.error, UnicodeDecodeError) as exc:
        raise SnapshotError(f"cannot read snapshot {path.name}: {type(exc).__name__}") from exc
    header_line, _, body = text.partition("\n")
    header = _parse_json(header_line, "header")
    if header.get("format") != FORMAT:
        raise SnapshotError(f"snapshot format {header.get('format')!r}, expected {FORMAT}")
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != header.get("body_sha256"):
        raise SnapshotError("snapshot body does not match its header hash; rebuild it")
    model = header.get("model")
    if model not in MODELS:
        raise SnapshotError(f"snapshot is for unknown embedding model {model!r}")
    tables: dict[str, list[dict[str, object]]] = {table: [] for table in _COLUMNS}
    for number, line in enumerate(body.splitlines(), start=2):
        table, row = _parse_row(line, number)
        tables[table].append(row)
    counts = {table: len(rows) for table, rows in tables.items()}
    if counts != header.get("counts"):
        raise SnapshotError(f"snapshot row counts {counts} differ from its header")
    rows = SnapshotRows(
        filings=tuple(_with_dates(r) for r in tables["filings"]),
        chunks=tuple(tables["chunks"]),
        embeddings=tuple(_embedding_row(r, model) for r in tables["chunk_embeddings"]),
    )
    vectors = tuple(_query_vector(r) for r in tables[_QUERY_TABLE])
    return Snapshot(hashlib.sha256(raw).hexdigest(), model, rows, vectors)


def load_snapshot(conn: psycopg.Connection, path: Path) -> Snapshot:
    """Check the snapshot at *path*, apply the schema, and insert its rows, then commit.

    Raises ``SnapshotError`` if the file fails ``read_snapshot``, the live schema
    differs from the code, the tables already hold filings, a row breaks a
    schema CHECK, or a vector was made from other text; nothing is then inserted.
    """
    snapshot = read_snapshot(path)
    try:
        apply_schema(conn)
        import_rows(conn, snapshot.rows, snapshot.model)
    except (ValueError, SchemaMismatch) as exc:
        raise SnapshotError(f"cannot load snapshot: {exc}") from exc
    except psycopg.Error as exc:
        # Only the primary message: Postgres's detail quotes the whole failing row.
        message = exc.diag.message_primary or type(exc).__name__
        raise SnapshotError(f"snapshot rows fail a schema check: {message}") from exc
    return snapshot


def _parse_json(line: str, what: str) -> dict[str, object]:
    try:
        obj = json.loads(line)
    except ValueError as exc:
        raise SnapshotError(f"snapshot {what} is not JSON") from exc
    if not isinstance(obj, dict):
        raise SnapshotError(f"snapshot {what} is not a JSON object")
    return obj


def _parse_row(line: str, number: int) -> tuple[str, dict[str, object]]:
    obj = _parse_json(line, f"line {number}")
    table, row = obj.get("table"), obj.get("row")
    if set(obj) != {"table", "row"} or table not in _COLUMNS or not isinstance(row, dict):
        raise SnapshotError(f"snapshot line {number} is not a table row")
    if set(row) != _COLUMNS[table]:
        raise SnapshotError(f"snapshot line {number}: {table} row has unexpected columns")
    return table, row


def _with_dates(row: dict[str, object]) -> dict[str, object]:
    try:
        return {**row, **{c: date.fromisoformat(str(row[c])) for c in _DATE_COLUMNS}}
    except ValueError as exc:
        raise SnapshotError(f"filing {row['accession_no']} has a bad date") from exc


def _embedding_row(row: dict[str, object], model: str) -> dict[str, object]:
    if row["model"] != model:
        raise SnapshotError(f"chunk {row['chunk_id']} vector is for {row['model']}, not {model}")
    return row


def _query_vector(row: dict[str, object]) -> QueryVector:
    model, sha, vector, tokens = (row[c] for c in _QUERY_COLUMNS)
    if model not in MODELS or not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        raise SnapshotError("snapshot query vector has a bad model or text hash")
    dims = MODELS[model].dimensions
    if not isinstance(vector, list) or len(vector) != dims or not all(
        _is_number(x) and math.isfinite(x) for x in vector
    ):
        raise SnapshotError(f"snapshot query vector {sha[:12]} is not {dims} finite numbers")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 0:
        raise SnapshotError(f"snapshot query vector {sha[:12]} has a bad api_token_count")
    return QueryVector(model, sha, tuple(float(x) for x in vector), tokens)


def _is_number(value: object) -> bool:
    """An int or float; JSON ``true`` and ``false`` are not numbers."""
    return isinstance(value, int | float) and not isinstance(value, bool)
