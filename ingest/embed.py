"""Embed every chunk into ``chunk_embeddings`` (Step 5, ADR-0002).

Usage (needs ``VOYAGE_API_KEY``, ``DATABASE_URL`` and the network)::

    uv run --env-file .env python -m ingest.embed

Each chunk's text is read through the same offsets ``resolve()`` uses.  Each
chunk is one Voyage request, so the stored token count is the one the API
reported for that chunk alone (Voyage reports usage per request, not per input).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass

import psycopg

from ingest.parsed_files import text_sha256
from ingest.store import (
    ChunkForEmbedding,
    SchemaMismatch,
    apply_schema,
    chunks_for_embedding,
    save_embedding,
)
from ingest.voyage import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    MODELS,
    Embedder,
    VoyageClient,
    VoyageError,
)

logger = logging.getLogger(__name__)

# The stored token counts are cl100k_base, not Voyage's tokenizer, so the check
# before any call keeps a 2x margin under the model's context.  The margin is an
# assumption, not a measured ratio.  Truncation is off, so a chunk that still
# exceeds the real limit makes the API refuse it rather than embed part of it.
CONTEXT_MARGIN = 2
TOO_LONG_LISTED = 10  # chunk IDs quoted in the ChunkTooLong message
PROGRESS_EVERY = 100
EXIT_USAGE = 2
EXIT_EMBED_ERROR = 3
_ERROR_CHARS = 500


class ChunkTooLong(ValueError):
    """A chunk is over the model's input limit; nothing was embedded."""


@dataclass(frozen=True)
class EmbedSummary:
    """What one run did."""

    embedded: int
    skipped: int  # stored vector's text hash still matches the chunk
    api_tokens: int


def _check_lengths(chunks: list[ChunkForEmbedding], limit: int) -> None:
    too_long = [c for c in chunks if c.token_count > limit]
    if too_long:
        listed = ", ".join(f"{c.chunk_id} ({c.token_count})" for c in too_long[:TOO_LONG_LISTED])
        raise ChunkTooLong(
            f"{len(too_long)} chunks over {limit} cl100k tokens, nothing embedded: {listed}"
        )


def embed_chunks(
    conn: psycopg.Connection,
    embedder: Embedder,
    model: str,
    max_input_tokens: int | None = None,
) -> EmbedSummary:
    """Embed each chunk with no current vector for *model*; commit after each one.

    A chunk is current when its stored ``text_sha256`` matches its text now.
    Before any call, raises ``ChunkTooLong`` if any chunk's stored token count
    is over *max_input_tokens* (default: the model's context over
    ``CONTEXT_MARGIN``).  The check covers every chunk, not only pending ones,
    so an oversized chunk is reported even when its vector is current.
    """
    if model not in MODELS:
        raise ValueError(f"unknown embedding model {model!r}")
    limit = (
        MODELS[model].context_tokens // CONTEXT_MARGIN
        if max_input_tokens is None else max_input_tokens
    )
    chunks = chunks_for_embedding(conn, model)
    _check_lengths(chunks, limit)
    pending = [c for c in chunks if c.stored_sha256 != text_sha256(c.text)]
    api_tokens = 0
    for done, chunk in enumerate(pending, start=1):
        embedding = embedder.embed_document(chunk.text, model)
        save_embedding(
            conn, chunk.chunk_id, model, chunk.text, embedding.vector, embedding.api_token_count
        )
        api_tokens += embedding.api_token_count
        if done % PROGRESS_EVERY == 0:
            logger.info("embedded %d of %d chunks", done, len(pending))
    return EmbedSummary(
        embedded=len(pending), skipped=len(chunks) - len(pending), api_tokens=api_tokens
    )


def _connect(url: str) -> psycopg.Connection | None:
    """Open a connection; on failure print only the error type (messages can quote the URL)."""
    try:
        return psycopg.connect(url)
    except psycopg.Error as exc:
        print(f"could not connect to the database: {type(exc).__name__}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Exit codes: 0 ok; 2 bad input (no API key, no DATABASE_URL, or a database
    that refuses the connection); 3 embedding error.  Vectors committed before
    an error stay stored, and a rerun skips them.
    """
    parser = argparse.ArgumentParser(description="Embed every chunk into chunk_embeddings.")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request

    for name in (API_KEY_ENV, "DATABASE_URL"):
        if not os.environ.get(name):
            print(f"{name} is not set (run with: uv run --env-file .env ...)", file=sys.stderr)
            return EXIT_USAGE
    conn = _connect(os.environ["DATABASE_URL"])
    if conn is None:
        return EXIT_USAGE
    with VoyageClient.from_env() as client, conn:
        try:
            apply_schema(conn)
            summary = embed_chunks(conn, client, args.model)
        except (psycopg.Error, SchemaMismatch, ChunkTooLong, VoyageError) as exc:
            logger.error("embedding failed (%s): %.*s", type(exc).__name__, _ERROR_CHARS, exc)
            return EXIT_EMBED_ERROR
    print(
        f"{args.model}: embedded {summary.embedded}, skipped {summary.skipped} current, "
        f"{summary.api_tokens} API tokens"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
