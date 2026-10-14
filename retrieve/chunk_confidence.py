"""Each traversal chunk's best evidence confidence (Step 11).

A generated query returns chunk IDs, not the evidence that cited them, so the
confidence is read afterwards in one READ transaction: the best of the
``EVIDENCED_BY`` entries into the chunk and the evidence entries for that chunk
on edges touching the nodes it evidences (``stated`` > ``implied`` >
``uncertain``).  A chunk no evidence cites keeps None.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from neo4j import Driver

from graph.connection import DATABASE, read_session
from graph.ontology import CONFIDENCE_RANK, Confidence
from retrieve.arm import RetrievedChunk

_QUERY = """UNWIND $ids AS id
MATCH (c:Chunk {chunk_id: id})
RETURN id,
  COLLECT { MATCH (c)<-[e:EVIDENCED_BY]-() RETURN e.confidence } AS own,
  COLLECT { MATCH (c)<-[:EVIDENCED_BY]-()-[r]-() WHERE r.chunk_ids IS NOT NULL
            UNWIND range(0, size(r.chunk_ids) - 1) AS i
            WITH r, i WHERE r.chunk_ids[i] = id RETURN r.confidences[i] } AS edges"""


def with_confidence(driver: Driver, retrieved: Sequence[RetrievedChunk],
                    database: str = DATABASE) -> tuple[RetrievedChunk, ...]:
    """*retrieved* with each chunk's best confidence; raises ``Neo4jError`` or
    ``DriverError`` on failure and ``ValueError`` for an unknown confidence."""
    if not retrieved:
        return ()
    ids = [r.chunk_id for r in retrieved]
    with read_session(driver, database) as session:
        rows = session.execute_read(lambda tx: [r.data() for r in tx.run(_QUERY, ids=ids)])
    best = {row["id"]: _best([*row["own"], *row["edges"]]) for row in rows}
    return tuple(replace(r, confidence=best.get(r.chunk_id)) for r in retrieved)


def _best(levels: Sequence[str]) -> Confidence | None:
    known = [Confidence(level) for level in levels]
    return max(known, key=CONFIDENCE_RANK.__getitem__) if known else None
