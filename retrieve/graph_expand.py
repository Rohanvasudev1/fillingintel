"""From anchors to ranked chunks, for graph_local (Step 11, ADR-0007).

Expansion reads Neo4j in a READ session.  At 1 hop an anchor reaches its own
``EVIDENCED_BY`` chunks and the ``chunk_ids`` of every edge touching it,
``PERSISTS_AS`` included.  At 2 hops it also reaches the ``EVIDENCED_BY``
chunks of the nodes at the other end of those edges.

A chunk scores the sum over anchors of similarity x (1 + the number of that
anchor's edges citing it).  Chunks outside the question filter are dropped,
ties go to the smaller chunk ID, and the list is cut to *k*.

``chunk_support`` says, for the answer trace, which anchor and which edges gave
each kept chunk its score (Step 14).  It reads what ranking read and changes
nothing about the ranking.
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
from retrieve.arm import Anchor, ChunkSupport, RetrievedChunk, SupportEdge

HOP_CHOICES = (1, 2)
SCORE_DECIMALS = 9  # scores are compared after rounding, so float noise never breaks a tie
# The other end of an edge, as the answer trace names it: label, identity, name, and
# whether the anchor is the edge's start.
_END = ("labels({o})[0], coalesce({o}.key, {o}.cik, {o}.accession_no), "
        "coalesce({o}.name, {o}.title, {o}.concept), startNode(r) = a")
_EVIDENCE = "COLLECT { MATCH (a)-[e:EVIDENCED_BY]->(c:Chunk) RETURN [c.chunk_id, e.confidence] }"
_EDGES = ("COLLECT { MATCH (a)-[r]-(o) WHERE r.chunk_ids IS NOT NULL "
          f"RETURN [r.chunk_ids, r.confidences, type(r), {_END.format(o='o')}] }}")
_NEIGHBOURS = ("COLLECT { MATCH (a)-[r]-(n)-[e:EVIDENCED_BY]->(c:Chunk) "
               "WHERE r.chunk_ids IS NOT NULL AND n <> a "
               f"RETURN [c.chunk_id, e.confidence, type(r), {_END.format(o='n')}] }}")

AnchorId = tuple[str, str]  # (label, key): keys are unique per label


@dataclass(frozen=True)
class Reach:
    """What one anchor reaches: each chunk's best confidence, and how many of the
    anchor's edges cite it.  The rest says how, for the answer trace only."""

    confidence: Mapping[str, Confidence] = field(default_factory=dict)
    edge_citations: Mapping[str, int] = field(default_factory=dict)
    evidenced: frozenset[str] = frozenset()  # the anchor's own EVIDENCED_BY chunks
    edges: Mapping[str, tuple[SupportEdge, ...]] = field(default_factory=dict)
    neighbours: Mapping[str, tuple[SupportEdge, ...]] = field(default_factory=dict)


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
    edges: dict[str, list[SupportEdge]] = defaultdict(list)
    neighbours: dict[str, list[SupportEdge]] = defaultdict(list)
    for chunk_id, confidence in row["own"]:
        _keep_best(best, chunk_id, Confidence(confidence))
    for chunk_id, confidence, *end in row["neighbours"]:
        _keep_best(best, chunk_id, Confidence(confidence))
        neighbours[chunk_id].append(_support_edge(*end))
    for chunk_ids, confidences, *end in row["edges"]:
        for chunk_id, confidence in zip(chunk_ids, confidences, strict=True):
            _keep_best(best, chunk_id, Confidence(confidence))
        for chunk_id in set(chunk_ids):
            citations[chunk_id] += 1
            edges[chunk_id].append(_support_edge(*end))
    return Reach(confidence=best, edge_citations=dict(citations),
                 evidenced=frozenset(chunk_id for chunk_id, _ in row["own"]),
                 edges={c: _sorted(e) for c, e in edges.items()},
                 neighbours={c: _sorted(e) for c, e in neighbours.items()})


def _support_edge(edge_type: str, label: str, identity: str, name: str | None,
                  outgoing: bool) -> SupportEdge:
    return SupportEdge(edge_type, label, identity, name, outgoing=outgoing)


def _sorted(edges: Sequence[SupportEdge]) -> tuple[SupportEdge, ...]:
    return tuple(sorted(edges, key=lambda e: (e.type, e.other_label, e.other_id, e.outgoing)))


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


def chunk_support(anchors: Sequence[Anchor], reach: Mapping[AnchorId, Reach],
                  retrieved: Sequence[RetrievedChunk]) -> tuple[ChunkSupport, ...]:
    """Each kept chunk's anchors, in retrieval order then anchor order, with the edges
    that cited it and each anchor's share of its score, as ``rank_chunks`` computed it."""
    support = []
    for chunk in retrieved:
        for anchor in anchors:
            anchor_reach = reach.get((anchor.label, anchor.key), Reach())
            if chunk.chunk_id not in anchor_reach.confidence:
                continue
            cited = anchor_reach.edge_citations.get(chunk.chunk_id, 0)
            support.append(ChunkSupport(
                chunk_id=chunk.chunk_id, anchor_label=anchor.label, anchor_key=anchor.key,
                evidenced=chunk.chunk_id in anchor_reach.evidenced,
                edges=anchor_reach.edges.get(chunk.chunk_id, ()),
                neighbours=anchor_reach.neighbours.get(chunk.chunk_id, ()),
                score=anchor.similarity * (1 + cited)))
    return tuple(support)
