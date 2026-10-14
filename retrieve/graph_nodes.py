"""Reading anchor candidates from the graph, and their embeddings from the disk cache
(Step 11).

Every extracted node is a candidate, read with its properties and the filings its
``EVIDENCED_BY`` chunks come from.  Node texts are embedded with Voyage
(``document`` input type) through the entity-embedding cache Step 9 uses, and
stored on disk, never in Neo4j; ``python -m retrieve.node_embed`` fills it.
"""
from __future__ import annotations

from collections.abc import Sequence

from neo4j import Driver, ManagedTransaction, NotificationDisabledClassification, unit_of_work

from graph.connection import DATABASE, WHOLE_GRAPH_TIMEOUT_S, read_session
from graph.cypher import quoted
from graph.ontology import LABELS, Kind
from ingest.voyage import InputType
from retrieve.anchors import AnchorNode, node_names, node_text

NODE_INPUT_TYPE: InputType = "document"
EXTRACTED_LABELS = tuple(d.name for d in LABELS if d.kind is Kind.EXTRACTED)


def read_anchor_nodes(driver: Driver, database: str = DATABASE) -> tuple[AnchorNode, ...]:
    """Every extracted node, by label then key, in one READ transaction.

    It reads the whole graph, so it sets ``WHOLE_GRAPH_TIMEOUT_S`` rather than
    taking the server's 15 s limit for generated queries.

    Raises ``Neo4jError`` or ``DriverError`` on failure, and ``ValueError`` for a
    node without the property its text is built from.
    """
    # A graph may lack some labels; their "does not exist" notifications are noise here.
    with read_session(driver, database, notifications_disabled_classifications=[
            NotificationDisabledClassification.UNRECOGNIZED]) as session:
        rows = session.execute_read(unit_of_work(timeout=WHOLE_GRAPH_TIMEOUT_S)(_read))
    return tuple(
        AnchorNode(
            key=props["key"], label=label, text=node_text(label, props),
            names=node_names(label, props), filings=frozenset(filings),
        )
        for label, props, filings in rows
    )


def _read(tx: ManagedTransaction) -> list[tuple[str, dict, list[str]]]:
    rows = []
    for label in EXTRACTED_LABELS:
        query = (f"MATCH (n:{quoted(label)}) RETURN n {{.*}} AS props, "
                 "COLLECT { MATCH (n)-[:EVIDENCED_BY]->(c:Chunk) "
                 "RETURN DISTINCT c.accession_no } AS filings ORDER BY n.key")
        rows += [(label, r["props"], r["filings"]) for r in tx.run(query)]
    return rows


def distinct_texts(nodes: Sequence[AnchorNode]) -> list[str]:
    """The nodes' texts, each once, in first-seen order."""
    return list(dict.fromkeys(n.text for n in nodes))
