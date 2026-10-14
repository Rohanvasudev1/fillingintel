"""A guarded query's rows as graph_traversal's ranked chunks (Step 11, ADR-0007).

Rows are read in the order the query returned them, and the chunk IDs within a
row in list order.  A chunk's first appearance sets its rank.  Chunks outside
the question filter, and strings that are not chunks of the corpus, are dropped
before the list is cut to *k*.  The score is 1 / rank, so it only restates the
order the query chose.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping

from graph.guard import CHUNK_IDS
from ingest.chunker import accession_of
from retrieve.arm import RetrievedChunk


def rank_rows(rows: Iterable[Mapping[str, object]], accession_nos: Collection[str] | None,
              known: Collection[str], k: int) -> tuple[RetrievedChunk, ...]:
    """At most *k* chunks, in first-appearance order, within the filter and the corpus."""
    allowed = None if accession_nos is None else frozenset(accession_nos)
    kept: dict[str, None] = {}
    for row in rows:
        for chunk_id in row[CHUNK_IDS]:
            if chunk_id not in known or chunk_id in kept:
                continue
            if allowed is not None and accession_of(chunk_id) not in allowed:
                continue
            kept[chunk_id] = None
            if len(kept) == k:
                return _ranked(kept)
    return _ranked(kept)


def _ranked(chunk_ids: Iterable[str]) -> tuple[RetrievedChunk, ...]:
    return tuple(RetrievedChunk(c, 1.0 / rank) for rank, c in enumerate(chunk_ids, start=1))
