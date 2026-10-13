"""The guarded write path into the graph (Step 7; rules of Step 10).

write_batch() and rebuild_graph() are the only ways into the graph. In one write
transaction write_batch():
1. refuses a graph without :GraphMeta or built under another ontology;
2. reads which cited chunks and edge endpoints already exist;
3. runs check_batch() and, on any violation, raises BatchRejected with the full
   list before any write;
4. merges nodes, then edges, then node evidence.
rebuild_graph() runs the same steps after deleting every node but :GraphMeta in
the same transaction, so a Step 10 load replaces the graph or changes nothing.

It writes exactly what the batch holds; combining evidence is the caller's job
(the Step 10 graph builder), so one item never depends on another. Nodes merge
on their key properties, and edges on (start, type, end), plus `role` where the
type has one, so one person's two roles at a company stay two edges. A batch
holding the same node key or edge identity twice is rejected by check_batch().
Every other property the ontology defines is set by name to the batch's value,
so a node or edge written again holds the batch's properties and an optional
property the batch leaves out (or gives as None) is removed. An extracted
edge's four evidence lists are set as given. Each piece of node evidence is one
EVIDENCED_BY edge per (node, chunk) whose span, confidence and prompt version
are the batch's; edges to chunks the batch does not cite are left alone (the
Step 10 rebuild deletes the old graph first).

Setting each property by name, never `SET +=`, keeps every write query free of
Eager operators (docs/research/neo4j-bulk-load.md, section 5); a test checks
the plans with EXPLAIN. Labels, edge types and property keys reach Cypher only
from the ontology, through graph.cypher.quoted(); every value is a parameter.
Dynamic labels are not used, since on Neo4j 5.26 a MATCH with them scans every
node (docs/research/neo4j-driver-and-constraints.md, 6.3).
"""
from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from typing import TypeVar

from neo4j import Driver, ManagedTransaction

from graph.batch import (
    CHUNK,
    CHUNK_ID,
    CHUNK_IDS,
    CONFIDENCE,
    EVIDENCE_LISTS,
    EVIDENCE_SPAN,
    EVIDENCED_BY,
    EXTRACT_PROMPT,
    IDENTITY_PROPERTIES,
    ONTOLOGY_VERSION_PROPERTY,
    Batch,
    EdgeRecord,
    NodeRecord,
    NodeRef,
    Violation,
    check_batch,
    identity_properties,
    is_well_formed,
    key_values,
)
from graph.connection import DATABASE
from graph.constraints import GRAPH_META, require_graph_meta
from graph.cypher import quoted
from graph.ontology import (
    EDGE_TYPES,
    EDGE_TYPES_BY_NAME,
    LABELS,
    LABELS_BY_NAME,
    ONTOLOGY_VERSION,
    Kind,
    Prop,
    PropType,
)

# Property names as they appear in Cypher text.
_CHUNK_ID, _VERSION = quoted(CHUNK_ID), quoted(ONTOLOGY_VERSION_PROPERTY)

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


class IncompleteWrite(RuntimeError):
    """A query wrote fewer rows than it was given; the transaction is rolled back."""


class GraphNotEmpty(RuntimeError):
    """The graph holds nodes besides :GraphMeta and the caller did not ask to replace them."""


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


def rebuild_graph(
    driver: Driver, batch: Batch, *, replace: bool = True, database: str = DATABASE,
) -> None:
    """Make *batch* the whole graph, keeping only :GraphMeta, in one transaction.

    With *replace* false, a graph holding any other node raises GraphNotEmpty.
    With it true, every node but :GraphMeta is deleted first, inside the same
    transaction, so the batch's checks see the emptied graph and any rejection
    or failure rolls the delete back: the previous graph stays. The delete is
    one full scan, which is what a rebuild needs. Same errors as write_batch().
    """
    with driver.session(database=database) as session:
        session.execute_write(_rebuild, batch, replace)


_OTHER_NODES = f"MATCH (n) WHERE NOT n:{quoted(GRAPH_META)}"


def _rebuild(tx: ManagedTransaction, batch: Batch, replace: bool) -> None:
    require_graph_meta(tx)
    if replace:
        tx.run(f"{_OTHER_NODES} DETACH DELETE n").consume()
    else:
        found = tx.run(f"{_OTHER_NODES} RETURN count(n) AS n").single(strict=True)["n"]
        if found:
            raise GraphNotEmpty(f"the graph holds {found} nodes besides :{GRAPH_META}")
    _write(tx, batch)


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
        raise IncompleteWrite(f"wrote {record['written']} of {len(rows)} rows: {query}")


# ── reading what already exists ────────────────────────────────────────────────

def _existing_chunk_ids(tx: ManagedTransaction, batch: Batch) -> frozenset[str]:
    from_nodes = {e.chunk_id for n in batch.nodes for e in n.evidence}
    from_edges = {
        c for e in batch.edges
        if isinstance(chunk_ids := e.properties.get(CHUNK_IDS), list | tuple)
        for c in chunk_ids
    }
    ids = sorted(c for c in from_nodes | from_edges if isinstance(c, str))
    if not ids:
        return frozenset()
    result = tx.run(
        f"MATCH (c:{quoted(CHUNK)}) WHERE c.{_CHUNK_ID} IN $ids RETURN c.{_CHUNK_ID} AS id",
        ids=ids,
    )
    return frozenset(r["id"] for r in result)


def _existing_nodes(tx: ManagedTransaction, batch: Batch) -> frozenset[NodeRef]:
    # check_batch reports malformed references; only well-formed ones are looked up.
    refs = {r for e in batch.edges for r in (e.start, e.end) if is_well_formed(r)}
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

def write_queries() -> tuple[str, ...]:
    """Every query the write path can run: per label, per extracted label's evidence, per edge
    type and endpoint pair. It builds them with the functions _write uses; a new kind of
    write query must be added here too, so the plan test sees it.
    """
    return (
        *(_node_query(label.name) for label in LABELS),
        *(_node_evidence_query(label.name) for label in LABELS if label.kind is Kind.EXTRACTED),
        *(
            _edge_query(edge.name, start, end)
            for edge in EDGE_TYPES if edge.name != EVIDENCED_BY
            for start in edge.starts for end in edge.ends
        ),
    )


def _key_map(label: str, source: str) -> str:
    """A Cypher map of *label*'s key properties read from the map *source*."""
    keys = LABELS_BY_NAME[label].keys
    return "{" + ", ".join(f"{quoted(k)}: {source}.{quoted(k)}" for k in keys) + "}"


def _named_sets(target: str, names: Iterable[str], source: str) -> list[str]:
    """`SET` items giving each property in *names* of *target* its value in map *source*."""
    return [f"{target}.{quoted(n)} = {source}.{quoted(n)}" for n in names]


def _set_clause(target: str, items: list[str]) -> str:
    """A `SET` of *items* plus *target*'s ontology version."""
    return "SET " + ", ".join([*items, f"{target}.{_VERSION} = $version"]) + " "


def _db_value(prop: Prop, value: object) -> object:
    """*value* as Neo4j should store it: FLOAT as a float, lists as lists, None as None."""
    if value is None:
        return None
    if prop.type is PropType.FLOAT:
        return float(value)  # type: ignore[arg-type]  # check_batch allows int | float
    if prop.type is PropType.LIST_STRING:
        return list(value)  # type: ignore[call-overload]
    return value


def _settable(props: Iterable[Prop], skip: Iterable[str]) -> tuple[Prop, ...]:
    """*props* the write path sets by name: all but *skip* and ontology_version."""
    left_out = {*skip, ONTOLOGY_VERSION_PROPERTY}
    return tuple(p for p in props if p.name not in left_out)


def _db_properties(props: tuple[Prop, ...], values: Mapping[str, object]) -> dict[str, object]:
    """Every property in *props* with its value in *values*, None when absent."""
    return {p.name: _db_value(p, values.get(p.name)) for p in props}


def _node_properties(label: str) -> tuple[Prop, ...]:
    definition = LABELS_BY_NAME[label]
    return _settable(definition.properties, definition.keys)


def _node_query(label: str) -> str:
    names = [p.name for p in _node_properties(label)]
    return (
        f"UNWIND $rows AS row MERGE (n:{quoted(label)} {_key_map(label, 'row.key')}) "
        f"{_set_clause('n', _named_sets('n', names, 'row.props'))}"
        "RETURN count(*) AS written"
    )


def _merge_nodes(tx: ManagedTransaction, label: str, nodes: list[NodeRecord]) -> None:
    props = _node_properties(label)
    rows = [
        {"key": key_values(label, n.properties), "props": _db_properties(props, n.properties)}
        for n in nodes
    ]
    _run_rows(tx, _node_query(label), rows)


def _edge_properties(edge_type: str) -> tuple[Prop, ...]:
    """An edge type's properties set by name; identity and evidence lists are set apart."""
    return _settable(EDGE_TYPES_BY_NAME[edge_type].properties,
                     (*IDENTITY_PROPERTIES, *EVIDENCE_LISTS))


def _edge_query(edge_type: str, start: str, end: str) -> str:
    definition = EDGE_TYPES_BY_NAME[edge_type]
    identity = identity_properties(edge_type)
    identity_map = ", ".join(f"{quoted(p)}: row.identity.{quoted(p)}" for p in identity)
    names = [p.name for p in _edge_properties(edge_type)]
    evidence = EVIDENCE_LISTS if definition.kind is Kind.EXTRACTED else ()
    sets = [*_named_sets("r", names, "row.props"), *_named_sets("r", evidence, "row.evidence")]
    return (
        f"UNWIND $rows AS row "
        f"MATCH (a:{quoted(start)} {_key_map(start, 'row.start')}) "
        f"MATCH (b:{quoted(end)} {_key_map(end, 'row.end')}) "
        f"MERGE (a)-[r:{quoted(edge_type)}{' {' + identity_map + '}' if identity else ''}]->(b) "
        f"{_set_clause('r', sets)}"
        "RETURN count(*) AS written"
    )


def _edge_row(edge: EdgeRecord) -> dict:
    return {
        "start": edge.start.key_dict(),
        "end": edge.end.key_dict(),
        "identity": {p: edge.properties[p] for p in identity_properties(edge.type)},
        "props": _db_properties(_edge_properties(edge.type), edge.properties),
        "evidence": {name: list(edge.properties.get(name, ())) for name in EVIDENCE_LISTS},
    }


def _merge_edges(
    tx: ManagedTransaction, edge_type: str, start: str, end: str, edges: list[EdgeRecord],
) -> None:
    _run_rows(tx, _edge_query(edge_type, start, end), [_edge_row(e) for e in edges])


def _node_evidence_query(label: str) -> str:
    return (
        f"UNWIND $rows AS row "
        f"MATCH (n:{quoted(label)} {_key_map(label, 'row.key')}) "
        f"MATCH (c:{quoted(CHUNK)} {{{_CHUNK_ID}: row.chunk_id}}) "
        f"MERGE (n)-[e:{quoted(EVIDENCED_BY)}]->(c) "
        f"SET e.{quoted(EVIDENCE_SPAN)} = row.span, "
        f"e.{quoted(CONFIDENCE)} = row.confidence, "
        f"e.{quoted(EXTRACT_PROMPT)} = row.extract_prompt, e.{_VERSION} = $version "
        "RETURN count(*) AS written"
    )


def _merge_node_evidence(tx: ManagedTransaction, label: str, nodes: list[NodeRecord]) -> None:
    _run_rows(tx, _node_evidence_query(label), [
        {"key": key_values(label, n.properties), "chunk_id": e.chunk_id, "span": e.span,
         "confidence": e.confidence, "extract_prompt": e.extract_prompt}
        for n in nodes for e in n.evidence
    ])
