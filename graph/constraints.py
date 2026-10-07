"""Uniqueness constraints and the :GraphMeta node, applied and checked (Step 7).

Neo4j Community enforces only uniqueness, so each label gets one uniqueness
constraint on its key properties, named from the ontology. `CREATE ... IF NOT
EXISTS` silently keeps any constraint that already has the name or the schema,
whatever its definition (docs/research/neo4j-driver-and-constraints.md, 2.3),
so apply_constraints() reads SHOW CONSTRAINTS back and compares it with the
code. The single :GraphMeta node records the ontology version and schema hash
the graph was built under; a graph from another ontology is refused, never
migrated here (Step 10 owns that).

Labels, properties and constraint names reach Cypher only from the ontology,
checked against a plain-identifier pattern and backtick-quoted; values are
parameters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from neo4j import Driver, ManagedTransaction

from graph.connection import DATABASE
from graph.cypher import quoted as _checked
from graph.ontology import EDGE_TYPES, LABELS, ONTOLOGY_VERSION
from graph.schema_text import SCHEMA_TEXT_SHA256

_GRAPH_META = "GraphMeta"


@dataclass(frozen=True, slots=True)
class ConstraintDef:
    """One node uniqueness constraint as Neo4j reports it in SHOW CONSTRAINTS."""

    name: str
    label: str
    properties: tuple[str, ...]
    type: str = "UNIQUENESS"
    entity_type: str = "NODE"

    def __str__(self) -> str:
        return (f"{self.name}: {self.type} {self.entity_type} "
                f"{self.label}({', '.join(self.properties)})")


def _snake(label: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", label).lower()


EXPECTED_CONSTRAINTS: tuple[ConstraintDef, ...] = tuple(
    ConstraintDef(f"{_snake(d.name)}_{'_'.join(d.keys)}_unique", d.name, d.keys)
    for d in LABELS
)
_EXPECTED_NAMES = frozenset(c.name for c in EXPECTED_CONSTRAINTS)
# A constraint on any of these that the code doesn't define is reported as extra.
_ONTOLOGY_NAMES = (
    frozenset(d.name for d in LABELS) | frozenset(d.name for d in EDGE_TYPES) | {_GRAPH_META}
)


class ConstraintMismatch(RuntimeError):
    """The database's constraints on ontology labels differ from the code."""

    def __init__(
        self,
        missing: tuple[ConstraintDef, ...],
        different: tuple[ConstraintDef, ...],
        extra: tuple[ConstraintDef, ...],
    ) -> None:
        self.missing, self.different, self.extra = missing, different, extra
        lines = ["Neo4j constraints differ from the ontology:"]
        lines += [f"  missing {c}" for c in missing]
        lines += [f"  different, found {c}" for c in different]
        lines += [f"  extra {c}" for c in extra]
        super().__init__("\n".join(lines))


class GraphMetaMismatch(RuntimeError):
    """:GraphMeta records another ontology, or there is more than one."""


def _create_statement(c: ConstraintDef) -> str:
    keys = ", ".join(f"n.{_checked(p)}" for p in c.properties)
    return (f"CREATE CONSTRAINT {_checked(c.name)} IF NOT EXISTS "
            f"FOR (n:{_checked(c.label)}) REQUIRE ({keys}) IS UNIQUE")


def _found_constraints(driver: Driver, database: str) -> tuple[ConstraintDef, ...]:
    records, _, _ = driver.execute_query(
        "SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties "
        "RETURN name, type, entityType, labelsOrTypes, properties",
        database_=database,
    )
    return tuple(
        ConstraintDef(
            r["name"], ", ".join(r["labelsOrTypes"] or []), tuple(r["properties"] or ()),
            r["type"], r["entityType"],
        )
        for r in records
        if _ONTOLOGY_NAMES.intersection(r["labelsOrTypes"] or ())
        or r["name"] in _EXPECTED_NAMES
    )


def _compare(found: tuple[ConstraintDef, ...]) -> ConstraintMismatch | None:
    by_name = {c.name: c for c in found}
    missing = tuple(c for c in EXPECTED_CONSTRAINTS if c.name not in by_name)
    different = tuple(
        by_name[c.name] for c in EXPECTED_CONSTRAINTS
        if c.name in by_name and by_name[c.name] != c
    )
    extra = tuple(c for c in found if c.name not in _EXPECTED_NAMES)
    if missing or different or extra:
        return ConstraintMismatch(missing, different, extra)
    return None


def _check_graph_meta(rows: list[dict]) -> None:
    if len(rows) > 1:
        raise GraphMetaMismatch(f"found {len(rows)} :{_GRAPH_META} nodes; expected one")
    expected = {"ontology_version": ONTOLOGY_VERSION, "schema_sha256": SCHEMA_TEXT_SHA256}
    if rows and rows[0] != expected:
        raise GraphMetaMismatch(
            f"the graph was built under ontology {rows[0]}, but the code is {expected}. "
            "Moving a graph to a new ontology is an explicit step, not done here."
        )


def require_graph_meta(tx: ManagedTransaction) -> None:
    """Raise GraphMetaMismatch unless :GraphMeta is present and records this ontology.

    The write path calls this inside its transaction, so a batch never lands in
    a graph without the constraints or one built under another ontology.
    """
    result = tx.run(
        f"MATCH (m:{_checked(_GRAPH_META)}) "
        "RETURN m.ontology_version AS ontology_version, m.schema_sha256 AS schema_sha256",
    )
    rows = [r.data() for r in result]
    if not rows:
        raise GraphMetaMismatch(
            f"no :{_GRAPH_META} node; run apply_constraints() before writing to the graph"
        )
    _check_graph_meta(rows)


def _graph_meta_rows(driver: Driver, database: str) -> list[dict]:
    records, _, _ = driver.execute_query(
        f"MATCH (m:{_checked(_GRAPH_META)}) "
        "RETURN m.ontology_version AS ontology_version, m.schema_sha256 AS schema_sha256",
        database_=database,
    )
    return [r.data() for r in records]


def _merge_graph_meta(tx: ManagedTransaction) -> None:
    result = tx.run(
        f"MERGE (m:{_checked(_GRAPH_META)}) "
        "ON CREATE SET m.ontology_version = $version, m.schema_sha256 = $sha256 "
        "RETURN m.ontology_version AS ontology_version, m.schema_sha256 AS schema_sha256",
        version=ONTOLOGY_VERSION, sha256=SCHEMA_TEXT_SHA256,
    )
    # Raising here rolls the transaction back, so an existing node is never
    # overwritten. Nothing stops two concurrent first runs from each creating
    # one; the next call then refuses on the count.
    _check_graph_meta([r.data() for r in result])


def apply_constraints(driver: Driver, database: str = DATABASE) -> None:
    """Create the ontology's constraints, check them, then create :GraphMeta.

    A :GraphMeta from another ontology is refused before anything changes. On a
    ConstraintMismatch the ontology's own constraints may already have been
    created, but :GraphMeta is not. Running it again on a matching database
    changes nothing.
    """
    _check_graph_meta(_graph_meta_rows(driver, database))
    for c in EXPECTED_CONSTRAINTS:
        driver.execute_query(_create_statement(c), database_=database)
    mismatch = _compare(_found_constraints(driver, database))
    if mismatch:
        raise mismatch
    with driver.session(database=database) as session:
        session.execute_write(_merge_graph_meta)
