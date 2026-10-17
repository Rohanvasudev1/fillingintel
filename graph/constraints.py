"""Uniqueness constraints and the :GraphMeta node, applied and checked (Step 7).

Neo4j Community enforces only uniqueness, so each label gets one uniqueness
constraint on its key properties, named from the ontology. `CREATE ... IF NOT
EXISTS` silently keeps any constraint that already has the name or the schema,
whatever its definition (docs/research/neo4j-driver-and-constraints.md, 2.3),
so apply_constraints() reads SHOW CONSTRAINTS back and compares it with the
code. The single :GraphMeta node records the ontology version and schema hash
the graph was built under; a graph from another ontology is refused, except
that a rebuild moves an older one to the code's version (prepare_upgrade() and
upgrade_graph_meta(), Step 11).

Labels, properties and constraint names reach Cypher only from the ontology,
checked against a plain-identifier pattern and backtick-quoted; values are
parameters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from neo4j import Driver, ManagedTransaction

from graph.connection import DATABASE
from graph.cypher import quoted
from graph.ontology import EDGE_TYPES, LABELS, ONTOLOGY_VERSION
from graph.schema_text import SCHEMA_TEXT_SHA256

GRAPH_META = "GraphMeta"
GRAPH_META_QUERY = (
    f"MATCH (m:{quoted(GRAPH_META)}) "
    "RETURN m.ontology_version AS ontology_version, m.schema_sha256 AS schema_sha256"
)
# Every node of the graph proper; :GraphMeta is bookkeeping.
OTHER_NODES = f"MATCH (n) WHERE NOT n:{quoted(GRAPH_META)}"
# The load report a rebuild wrote, such as "load-run6" (Step 13). graph.load records it
# in the rebuild transaction; the graph arms read it for their results header.
LOAD_RUN_PROPERTY = "load_run"
LOAD_RUN_QUERY = (f"MATCH (m:{quoted(GRAPH_META)}) "
                  f"RETURN m.{quoted(LOAD_RUN_PROPERTY)} AS load_run")
_LOAD_RUN_NAME = re.compile(r"load-run[1-9]\d*")


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
    frozenset(d.name for d in LABELS) | frozenset(d.name for d in EDGE_TYPES) | {GRAPH_META}
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
    keys = ", ".join(f"n.{quoted(p)}" for p in c.properties)
    return (f"CREATE CONSTRAINT {quoted(c.name)} IF NOT EXISTS "
            f"FOR (n:{quoted(c.label)}) REQUIRE ({keys}) IS UNIQUE")


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


def graph_meta_problem(rows: list[dict], *, required: bool) -> str | None:
    """Why the :GraphMeta rows from GRAPH_META_QUERY don't match the code, or None.

    No rows is a problem only when *required*: a fresh database has none yet.
    """
    if not rows and required:
        return f"no :{GRAPH_META} node; run apply_constraints() before using the graph"
    if len(rows) > 1:
        return f"found {len(rows)} :{GRAPH_META} nodes; expected one"
    expected = {"ontology_version": ONTOLOGY_VERSION, "schema_sha256": SCHEMA_TEXT_SHA256}
    if rows and rows[0] != expected:
        return (
            f"the graph was built under ontology {rows[0]}, but the code is {expected}. "
            "Moving a graph to a new ontology is an explicit step, not done here."
        )
    return None


def _check_graph_meta(rows: list[dict], *, required: bool = False) -> None:
    problem = graph_meta_problem(rows, required=required)
    if problem:
        raise GraphMetaMismatch(problem)


def require_graph_meta(tx: ManagedTransaction) -> None:
    """Raise GraphMetaMismatch unless :GraphMeta is present and records this ontology.

    The write path calls this inside its transaction, so a batch never lands in
    a graph without the constraints or one built under another ontology.
    """
    _check_graph_meta([r.data() for r in tx.run(GRAPH_META_QUERY)], required=True)


def _graph_meta_rows(driver: Driver, database: str) -> list[dict]:
    records, _, _ = driver.execute_query(GRAPH_META_QUERY, database_=database)
    return [r.data() for r in records]


def _merge_graph_meta(tx: ManagedTransaction) -> None:
    result = tx.run(
        f"MERGE (m:{quoted(GRAPH_META)}) "
        "ON CREATE SET m.ontology_version = $version, m.schema_sha256 = $sha256 "
        "RETURN m.ontology_version AS ontology_version, m.schema_sha256 AS schema_sha256",
        version=ONTOLOGY_VERSION, sha256=SCHEMA_TEXT_SHA256,
    )
    # Raising here rolls the transaction back, so an existing node is never
    # overwritten. Nothing stops two concurrent first runs from each creating
    # one; the next call then refuses on the count.
    _check_graph_meta([r.data() for r in result])


def load_run_problem(rows: list[dict]) -> str | None:
    """Why the rows from LOAD_RUN_QUERY name no load run, or None.

    Only a graph with no :GraphMeta or several is left to graph_meta_problem().
    """
    if len(rows) != 1:
        return None
    load_run = rows[0]["load_run"]
    if load_run is None:
        return (f":{GRAPH_META} records no load run; rebuild the graph with "
                "python -m graph.load --rebuild")
    if not isinstance(load_run, str) or not _LOAD_RUN_NAME.fullmatch(load_run):
        return f":{GRAPH_META} load run {load_run!r} is not a load report name such as load-run6"
    return None


def read_load_run(tx: ManagedTransaction) -> str:
    """The load run :GraphMeta records; GraphMetaMismatch when it records none."""
    rows = [r.data() for r in tx.run(LOAD_RUN_QUERY)]
    if len(rows) != 1:
        raise GraphMetaMismatch(f"found {len(rows)} :{GRAPH_META} nodes; expected one")
    problem = load_run_problem(rows)
    if problem:
        raise GraphMetaMismatch(problem)
    return rows[0]["load_run"]


def record_load_run(tx: ManagedTransaction, load_run: str) -> None:
    """Record *load_run* on the one :GraphMeta node, inside the caller's transaction."""
    if not _LOAD_RUN_NAME.fullmatch(load_run):
        raise ValueError(f"{load_run!r} is not a load report name such as load-run6")
    record = tx.run(
        f"MATCH (m:{quoted(GRAPH_META)}) SET m.{quoted(LOAD_RUN_PROPERTY)} = $run "
        "RETURN count(m) AS n", run=load_run,
    ).single(strict=True)
    if record["n"] != 1:
        raise GraphMetaMismatch(f"found {record['n']} :{GRAPH_META} nodes; expected one")


def older_graph_meta_version(rows: list[dict]) -> int | None:
    """The version one :GraphMeta row records when it is an integer below the code's."""
    if len(rows) != 1:
        return None
    version = rows[0].get("ontology_version")
    if isinstance(version, int) and not isinstance(version, bool) and version < ONTOLOGY_VERSION:
        return version
    return None


def prepare_upgrade(driver: Driver, database: str = DATABASE) -> int | None:
    """Before a rebuild: the older version :GraphMeta records, or None when it is current.

    For an older version the constraints are re-applied and checked; they are
    derived from the labels alone, so for an ontology that adds no label this
    changes nothing. :GraphMeta itself is moved by upgrade_graph_meta() inside
    the rebuild transaction. A missing, newer, unparseable or otherwise
    different :GraphMeta raises GraphMetaMismatch before anything changes.
    """
    rows = _graph_meta_rows(driver, database)
    older = older_graph_meta_version(rows)
    if older is None:
        _check_graph_meta(rows, required=True)
        return None
    for c in EXPECTED_CONSTRAINTS:
        driver.execute_query(_create_statement(c), database_=database)
    mismatch = _compare(_found_constraints(driver, database))
    if mismatch:
        raise mismatch
    return older


def upgrade_graph_meta(tx: ManagedTransaction, older: int) -> None:
    """Record the code's ontology on a :GraphMeta that still records *older*.

    Run inside the rebuild transaction, so a failed rebuild keeps the old record.
    """
    record = tx.run(
        f"MATCH (m:{quoted(GRAPH_META)}) WHERE m.ontology_version = $older "
        "SET m.ontology_version = $version, m.schema_sha256 = $sha256 RETURN count(m) AS n",
        older=older, version=ONTOLOGY_VERSION, sha256=SCHEMA_TEXT_SHA256,
    ).single(strict=True)
    if record["n"] != 1:
        raise GraphMetaMismatch(f":{GRAPH_META} no longer records ontology {older}")


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
