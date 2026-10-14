"""From anchors to ranked chunks, for graph_local (Step 11, ADR-0007).

Expansion reads Neo4j in a READ session.  At 1 hop an anchor reaches its own
``EVIDENCED_BY`` chunks and the ``chunk_ids`` of every edge touching it,
``PERSISTS_AS`` included.  At 2 hops it also reaches the ``EVIDENCED_BY``
chunks of the nodes at the other end of those edges.

A chunk scores the sum over anchors of similarity x (1 + the number of that
anchor's edges citing it).  Chunks outside the question filter are dropped,
ties go to the smaller chunk ID, and the list is cut to *k*.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import groupby

from neo4j import Driver, ManagedTransaction

from graph.connection import DATABASE, read_session
from graph.cypher import quoted
from graph.ontology import CONFIDENCE_RANK, LABELS_BY_NAME, Confidence, Kind
from ingest.chunker import accession_of
from retrieve.arm import Anchor, RetrievedChunk

HOP_CHOICES = (1, 2)
SCORE_DECIMALS = 9  # scores are compared after rounding, so float noise never breaks a tie
_EVIDENCE = "COLLECT { MATCH (a)-[e:EVIDENCED_BY]->(c:Chunk) RETURN [c.chunk_id, e.confidence] }"
_EDGES = ("COLLECT { MATCH (a)-[r]-() WHERE r.chunk_ids IS NOT NULL "
          "RETURN [r.chunk_ids, r.confidences] }")
_NEIGHBOURS = ("COLLECT { MATCH (a)-[r]-(n)-[e:EVIDENCED_BY]->(c:Chunk) "
               "WHERE r.chunk_ids IS NOT NULL AND n <> a "
               "RETURN [c.chunk_id, e.confidence] }")

AnchorId = tuple[str, str]  # (label, key): keys are unique per label


@dataclass(frozen=True)
class Reach:
    """What one anchor reaches: each chunk's best confidence, and how many of the
    anchor's edges cite it."""

    confidence: Mapping[str, Confidence] = field(default_factory=dict)
    edge_citations: Mapping[str, int] = field(default_factory=dict)


def expand(driver: Driver, anchors: Sequence[Anchor], hops: int,
           database: str = DATABASE) -> dict[AnchorId, Reach]:
    """Each anchor's reach, read in one READ transaction; raises ``Neo4jError`` or
    ``DriverError`` on failure and ``ValueError`` for an unknown hop count or label."""
    if hops not in HOP_CHOICES:
        raise ValueError(f"hops must be one of {HOP_CHOICES}, not {hops}")
    by_label = groupby(sorted({(a.label, a.key) for a in anchors}), key=lambda a: a[0])
    keys = {label: [key for _, key in group] for label, group in by_label}
    queries = {label: _query(label, hops) for label in keys}
    with read_session(driver, database) as session:
        rows = session.execute_read(_read, queries, keys)
    return {(label, row["key"]): _reach(row) for label, label_rows in rows.items()
            for row in label_rows}


def _query(label: str, hops: int) -> str:
    definition = LABELS_BY_NAME.get(label)
    if definition is None or definition.kind is not Kind.EXTRACTED:
        raise ValueError(f"{label!r} is not an extracted label")
    neighbours = _NEIGHBOURS if hops == 2 else "[]"
    return (f"UNWIND $keys AS key MATCH (a:{quoted(label)} {{key: key}}) "
            f"RETURN a.key AS key, {_EVIDENCE} AS own, {_EDGES} AS edges, "
            f"{neighbours} AS neighbours")


def _read(tx: ManagedTransaction, queries: Mapping[str, str],
          keys: Mapping[str, list[str]]) -> dict[str, list[dict]]:
    return {label: [r.data() for r in tx.run(query, keys=keys[label])]
            for label, query in queries.items()}


def _reach(row: Mapping[str, list]) -> Reach:
    best: dict[str, Confidence] = {}
    citations: dict[str, int] = defaultdict(int)
    for chunk_id, confidence in (*row["own"], *row["neighbours"]):
        _keep_best(best, chunk_id, Confidence(confidence))
    for chunk_ids, confidences in row["edges"]:
        for chunk_id, confidence in zip(chunk_ids, confidences, strict=True):
            _keep_best(best, chunk_id, Confidence(confidence))
        for chunk_id in set(chunk_ids):
            citations[chunk_id] += 1
    return Reach(confidence=best, edge_citations=dict(citations))


def _keep_best(best: dict[str, Confidence], chunk_id: str, level: Confidence) -> None:
    if chunk_id not in best or CONFIDENCE_RANK[level] > CONFIDENCE_RANK[best[chunk_id]]:
        best[chunk_id] = level


def rank_chunks(anchors: Sequence[Anchor], reach: Mapping[AnchorId, Reach],
                accession_nos: Collection[str] | None, k: int) -> tuple[RetrievedChunk, ...]:
    """The top *k* chunks within the filter, best first, each with its best confidence."""
    allowed = None if accession_nos is None else frozenset(accession_nos)
    scores: dict[str, float] = defaultdict(float)
    confidence: dict[str, Confidence] = {}
    for anchor in anchors:
        anchor_reach = reach.get((anchor.label, anchor.key), Reach())
        for chunk_id, level in anchor_reach.confidence.items():
            if allowed is not None and accession_of(chunk_id) not in allowed:
                continue
            cited = anchor_reach.edge_citations.get(chunk_id, 0)
            scores[chunk_id] += anchor.similarity * (1 + cited)
            _keep_best(confidence, chunk_id, level)
    ranked = sorted(scores, key=lambda c: (-round(scores[c], SCORE_DECIMALS), c))[:k]
    return tuple(RetrievedChunk(c, scores[c], confidence[c]) for c in ranked)
