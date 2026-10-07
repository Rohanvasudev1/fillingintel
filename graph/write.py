"""The guarded write path into the graph (Step 7).

write_batch() is the one way into the graph. In one write transaction it:
1. refuses a graph without :GraphMeta or built under another ontology;
2. reads which cited chunks and edge endpoints already exist;
3. runs check_batch() and, on any violation, raises BatchRejected with the full
   list before any write;
4. merges nodes, then edges, then node evidence.

Nodes merge on their key properties; a node written again takes the batch's
property values. Edges merge on (start, type, end), plus `role` where the type
has one, so one person's two roles at a company stay two edges. An extracted
edge written again gets the batch's new chunk IDs appended with their spans;
chunk IDs it already lists are skipped (ADR-0004). Node evidence accumulates
the same way: one EVIDENCED_BY edge per (node, chunk), and the span written
first is kept.

Labels, edge types and property keys reach Cypher only from the ontology,
through graph.cypher.quoted(); every value is a parameter. Dynamic labels are
not used, since on Neo4j 5.26 a MATCH with them scans every node
(docs/research/neo4j-driver-and-constraints.md, 6.3).
"""
from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from typing import TypeVar

from neo4j import Driver, ManagedTransaction

from graph.batch import Batch, EdgeRecord, NodeRecord, NodeRef, Violation, check_batch
from graph.connection import DATABASE
from graph.constraints import require_graph_meta
from graph.cypher import quoted
from graph.ontology import (
    EDGE_TYPES_BY_NAME,
    LABELS_BY_NAME,
    ONTOLOGY_VERSION,
    Kind,
    Prop,
    PropType,
)

_CHUNK = "Chunk"
_EVIDENCED_BY = "EVIDENCED_BY"
_IDENTITY_PROPERTIES = ("role",)
_EVIDENCE_PROPERTIES = ("chunk_ids", "evidence_spans")

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


class BatchRejected(ValueError):
    """The batch breaks the ontology or evidence rules; nothing was written."""

    def __init__(self, violations: tuple[Violation, ...]) -> None:
        self.violations = violations
        lines = [f"batch rejected with {len(violations)} violations; nothing was written:"]
        super().__init__("\n".join([*lines, *(f"  {v}" for v in violations)]))


def write_batch(driver: Driver, batch: Batch, database: str = DATABASE) -> None:
    """Check *batch* and write it in one transaction, or raise and write nothing.

    Raises GraphMetaMismatch when the graph has no :GraphMeta or another
    ontology's, and BatchRejected listing every violation.
    """
    with driver.session(database=database) as session:
        session.execute_write(_write, batch)


def _write(tx: ManagedTransaction, batch: Batch) -> None:
    require_graph_meta(tx)
    violations = check_batch(batch, _existing_chunk_ids(tx, batch), _existing_nodes(tx, batch))
    if violations:
        raise BatchRejected(violations)
    for label, nodes in _grouped(batch.nodes, lambda n: n.label).items():
        _merge_nodes(tx, label, nodes)
    for (edge_type, start, end), edges in _grouped(
        batch.edges, lambda e: (e.type, e.start.label, e.end.label),
    ).items():
        _merge_edges(tx, edge_type, start, end, edges)
    for label, nodes in _grouped(
        [n for n in batch.nodes if n.evidence], lambda n: n.label,
    ).items():
        _merge_node_evidence(tx, label, nodes)


def _grouped(items: Iterable[T], key: Callable[[T], K]) -> dict[K, list[T]]:
    groups: dict[K, list[T]] = {}
    for item in items:
        groups.setdefault(key(item), []).append(item)
    return groups


def _run_rows(tx: ManagedTransaction, query: str, rows: list[dict]) -> None:
    """Run *query* over *rows*; a row that matched nothing aborts the transaction."""
    record = tx.run(query, rows=rows, version=ONTOLOGY_VERSION).single(strict=True)
    if record["written"] != len(rows):
        raise RuntimeError(f"wrote {record['written']} of {len(rows)} rows: {query}")


# ── reading what already exists ────────────────────────────────────────────────

def _existing_chunk_ids(tx: ManagedTransaction, batch: Batch) -> frozenset[str]:
    cited = {e.chunk_id for n in batch.nodes for e in n.evidence}
    for edge in batch.edges:
        chunk_ids = edge.properties.get("chunk_ids")
        if isinstance(chunk_ids, list | tuple):
            cited.update(chunk_ids)
    ids = sorted(c for c in cited if isinstance(c, str))
    if not ids:
        return frozenset()
    result = tx.run(
        f"MATCH (c:{quoted(_CHUNK)}) WHERE c.`chunk_id` IN $ids RETURN c.`chunk_id` AS id",
        ids=ids,
    )
    return frozenset(r["id"] for r in result)


def _lookup_ref(ref: NodeRef) -> bool:
    """Whether *ref* is well-formed enough to look up; check_batch reports the rest."""
    definition = LABELS_BY_NAME.get(ref.label)
    return (
        definition is not None
        and sorted(k for k, _ in ref.key) == sorted(definition.keys)
        and all(isinstance(v, str) for _, v in ref.key)
    )


def _existing_nodes(tx: ManagedTransaction, batch: Batch) -> frozenset[NodeRef]:
    refs = {r for e in batch.edges for r in (e.start, e.end) if _lookup_ref(r)}
    found: set[NodeRef] = set()
    for label, group in _grouped(sorted(refs, key=str), lambda r: r.label).items():
        result = tx.run(
            f"UNWIND $keys AS key MATCH (n:{quoted(label)} {_key_map(label, 'key')}) "
            "RETURN key",
            keys=[r.key_dict() for r in group],
        )
        found.update(NodeRef.of(label, **r["key"]) for r in result)
    return frozenset(found)


# ── writing ────────────────────────────────────────────────────────────────────

def _key_map(label: str, source: str) -> str:
    """A Cypher map of *label*'s key properties read from the map *source*."""
    keys = LABELS_BY_NAME[label].keys
    return "{" + ", ".join(f"{quoted(k)}: {source}.{quoted(k)}" for k in keys) + "}"


def _db_value(prop: Prop, value: object) -> object:
    """*value* as Neo4j should store it: FLOAT as a float, lists as lists."""
    if prop.type is PropType.FLOAT:
        return float(value)  # type: ignore[arg-type]  # check_batch allows int | float
    if prop.type is PropType.LIST_STRING:
        return list(value)  # type: ignore[call-overload]
    return value


def _db_properties(props: tuple[Prop, ...], values: Mapping[str, object]) -> dict[str, object]:
    defined = {p.name: p for p in props}
    return {name: _db_value(defined[name], value) for name, value in values.items()}


def _merge_nodes(tx: ManagedTransaction, label: str, nodes: list[NodeRecord]) -> None:
    definition = LABELS_BY_NAME[label]
    rows = [
        {"key": {k: n.properties[k] for k in definition.keys},
         "props": _db_properties(definition.properties, n.properties)}
        for n in nodes
    ]
    _run_rows(tx, (
        f"UNWIND $rows AS row MERGE (n:{quoted(label)} {_key_map(label, 'row.key')}) "
        "SET n += row.props, n.`ontology_version` = $version "
        "RETURN count(*) AS written"
    ), rows)


def _edge_query(edge_type: str, start: str, end: str) -> str:
    definition = EDGE_TYPES_BY_NAME[edge_type]
    identity = [p.name for p in definition.properties if p.name in _IDENTITY_PROPERTIES]
    identity_map = ", ".join(f"{quoted(p)}: row.props.{quoted(p)}" for p in identity)
    query = (
        f"UNWIND $rows AS row "
        f"MATCH (a:{quoted(start)} {_key_map(start, 'row.start')}) "
        f"MATCH (b:{quoted(end)} {_key_map(end, 'row.end')}) "
        f"MERGE (a)-[r:{quoted(edge_type)}{' {' + identity_map + '}' if identity else ''}]->(b) "
        "SET r += row.props, r.`ontology_version` = $version "
    )
    if definition.kind is Kind.EXTRACTED:
        query += (
            "WITH r, row, [i IN range(0, size(row.chunk_ids) - 1) "
            "WHERE NOT row.chunk_ids[i] IN coalesce(r.`chunk_ids`, [])] AS fresh "
            "SET r.`chunk_ids` = coalesce(r.`chunk_ids`, []) + [i IN fresh | row.chunk_ids[i]], "
            "r.`evidence_spans` = coalesce(r.`evidence_spans`, []) "
            "+ [i IN fresh | row.evidence_spans[i]] "
        )
    return query + "RETURN count(*) AS written"


def _edge_row(edge: EdgeRecord) -> dict:
    definition = EDGE_TYPES_BY_NAME[edge.type]
    plain = {k: v for k, v in edge.properties.items() if k not in _EVIDENCE_PROPERTIES}
    return {
        "start": edge.start.key_dict(),
        "end": edge.end.key_dict(),
        "props": _db_properties(definition.properties, plain),
        "chunk_ids": list(edge.properties.get("chunk_ids", ())),
        "evidence_spans": list(edge.properties.get("evidence_spans", ())),
    }


def _merge_edges(
    tx: ManagedTransaction, edge_type: str, start: str, end: str, edges: list[EdgeRecord],
) -> None:
    _run_rows(tx, _edge_query(edge_type, start, end), [_edge_row(e) for e in edges])


def _merge_node_evidence(tx: ManagedTransaction, label: str, nodes: list[NodeRecord]) -> None:
    keys = LABELS_BY_NAME[label].keys
    rows = [
        {"key": {k: n.properties[k] for k in keys}, "chunk_id": e.chunk_id, "span": e.span}
        for n in nodes for e in n.evidence
    ]
    _run_rows(tx, (
        f"UNWIND $rows AS row "
        f"MATCH (n:{quoted(label)} {_key_map(label, 'row.key')}) "
        f"MATCH (c:{quoted(_CHUNK)} {{`chunk_id`: row.chunk_id}}) "
        f"MERGE (n)-[e:{quoted(_EVIDENCED_BY)}]->(c) "
        "ON CREATE SET e.`evidence_span` = row.span, e.`ontology_version` = $version "
        "RETURN count(*) AS written"
    ), rows)
