"""Tests for write_batch() against the Neo4j test instance (Step 7).

They skip without NEO4J_TEST_URI; CI provides one. Locally:
`docker compose up -d neo4j-test`.
"""
import inspect
from collections import Counter
from dataclasses import replace
from datetime import date

import pytest
from neo4j.exceptions import ConstraintError
from neo4j.time import Date

import graph.write
from graph.batch import Batch, EdgeRecord, Evidence, NodeRecord, NodeRef, Rule
from graph.connection import DATABASE
from graph.constraints import GraphMetaMismatch, apply_constraints
from graph.ontology import ONTOLOGY_VERSION
from graph.write import BatchRejected, write_batch
from tests.graph_test_data import (
    COMPUTE,
    CUSTOMERS_CHUNK,
    HUANG,
    MDA_CUSTOMERS_CHUNK,
    MDA_SEGMENTS_CHUNK,
    NVDA_10K,
    NVIDIA,
    OFFICERS_CHUNK,
    PERIOD,
    QUOTES,
    SEGMENTS_CHUNK,
    SUPPLY_CHUNK,
    TOP_CUSTOMER_SHARE,
    TSMC,
    edge_evidence,
    evidence,
)
from tests.neo4j_fixtures import MARKER_LABEL


def _run(driver, query, **params):
    records, _, _ = driver.execute_query(query, params, database_=DATABASE)
    return [r.data() for r in records]


def _snapshot(driver):
    """Every node and edge but the marker, as sorted strings."""
    nodes = _run(driver, f"MATCH (n) WHERE NOT n:`{MARKER_LABEL}` "
                         "RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS p")
    edges = _run(driver, "MATCH (a)-[r]->(b) RETURN elementId(a) AS a, type(r) AS t, "
                         "properties(r) AS p, elementId(b) AS b")
    return sorted(map(str, nodes)), sorted(map(str, edges))


def _edges(driver, edge_type):
    return _run(driver, f"MATCH (a)-[r:`{edge_type}`]->(b) RETURN properties(r) AS p")


@pytest.fixture
def graph_driver(neo4j_driver):
    """The wiped test database with the ontology's constraints and :GraphMeta."""
    apply_constraints(neo4j_driver)
    return neo4j_driver


@pytest.fixture
def written(graph_driver, graph_test_batch):
    write_batch(graph_driver, graph_test_batch)
    return graph_driver


# ── a valid batch ──────────────────────────────────────────────────────────────

def test_a_valid_batch_reads_back_with_every_property(written, graph_test_batch):
    found = _run(written, f"MATCH (n) WHERE NOT n:`{MARKER_LABEL}` AND NOT n:GraphMeta "
                          "RETURN labels(n) AS labels, properties(n) AS p")
    expected = [
        {"labels": [n.label], "p": {**n.properties, "ontology_version": ONTOLOGY_VERSION}}
        for n in graph_test_batch.nodes
    ]
    assert sorted(map(_plain, found), key=str) == sorted(map(_plain, expected), key=str)

    counts = Counter(r["t"] for r in _run(written, "MATCH ()-[r]->() RETURN type(r) AS t"))
    expected_counts = Counter(e.type for e in graph_test_batch.edges)
    expected_counts["EVIDENCED_BY"] = sum(len(n.evidence) for n in graph_test_batch.nodes)
    assert counts == expected_counts


def _plain(row):
    """Neo4j dates as Python ones, ints as floats and keys sorted, so both sides compare."""
    props = {
        k: v.to_native() if isinstance(v, Date) else float(v) if k == "value" else v
        for k, v in sorted(row["p"].items())
    }
    return {"labels": row["labels"], "p": props}


def test_edges_and_node_evidence_carry_ontology_version_and_their_evidence(written):
    [supplies] = _edges(written, "SUPPLIES")
    assert supplies["p"] == {
        "chunk_ids": [SUPPLY_CHUNK], "evidence_spans": [QUOTES[SUPPLY_CHUNK]],
        "ontology_version": ONTOLOGY_VERSION,
    }
    [evidenced] = _run(written, "MATCH (:Organization {key: $key})-[e:EVIDENCED_BY]->"
                                "(c:Chunk) RETURN properties(e) AS p, c.chunk_id AS chunk",
                       key="org:tsmc")
    assert evidenced == {"p": {"evidence_span": QUOTES[SUPPLY_CHUNK],
                               "ontology_version": ONTOLOGY_VERSION},
                         "chunk": SUPPLY_CHUNK}
    assert [f["p"] for f in _edges(written, "FILED")] == [
        {"ontology_version": ONTOLOGY_VERSION}
    ] * 2


def test_a_whole_number_metric_value_is_stored_as_a_float(written):
    [row] = _run(written, "MATCH (m:MetricValue) RETURN m.value AS value, "
                          "m.value IS :: FLOAT AS is_float")
    assert row == {"value": 22.0, "is_float": True}
    assert isinstance(row["value"], float)


def test_a_filing_date_is_stored_as_a_date(written):
    [row] = _run(written, "MATCH (f:Filing {accession_no: $a}) RETURN f.filing_date AS d, "
                          "f.filing_date IS :: DATE AS is_date", a=NVDA_10K)
    assert row["is_date"] and row["d"].to_native() == date(2026, 2, 26)


def test_writing_the_same_batch_twice_changes_nothing(written, graph_test_batch):
    before = _snapshot(written)
    write_batch(written, graph_test_batch)
    assert _snapshot(written) == before


# ── rejected batches ───────────────────────────────────────────────────────────

def test_a_batch_with_one_bad_item_writes_nothing(graph_driver, graph_test_batch):
    bad = EdgeRecord("SUPPLIES", PERIOD, NVIDIA, edge_evidence(SUPPLY_CHUNK))
    before = _snapshot(graph_driver)

    with pytest.raises(BatchRejected) as exc:
        write_batch(graph_driver, replace(graph_test_batch,
                                          edges=(*graph_test_batch.edges, bad)))

    assert [v.rule for v in exc.value.violations] == [Rule.DISALLOWED_ENDPOINT]
    assert "SUPPLIES" in str(exc.value)
    assert _snapshot(graph_driver) == before


def test_a_rejected_batch_leaves_an_existing_graph_unchanged(written):
    before = _snapshot(written)
    batch = Batch(
        nodes=(NodeRecord("Organization", {"key": "org:samsung", "name": "Samsung"},
                          (Evidence(SUPPLY_CHUNK, "Samsung"),)),),
        edges=(EdgeRecord("SUPPLIES", NodeRef.of("Organization", key="org:samsung"), NVIDIA,
                          {"chunk_ids": [f"{NVDA_10K}:9999"], "evidence_spans": ["x"]}),),
    )
    with pytest.raises(BatchRejected) as exc:
        write_batch(written, batch)
    assert [v.rule for v in exc.value.violations] == [Rule.UNKNOWN_CHUNK]
    assert _snapshot(written) == before


def test_a_database_error_mid_batch_rolls_the_whole_batch_back(written):
    # A test-only constraint makes the second node fail inside the transaction,
    # after the first has been merged.
    _run(written, "CREATE CONSTRAINT test_org_name FOR (o:Organization) REQUIRE o.name IS UNIQUE")
    before = _snapshot(written)
    batch = Batch(nodes=(
        NodeRecord("Organization", {"key": "org:samsung", "name": "Samsung"},
                   (Evidence(SUPPLY_CHUNK, "Samsung"),)),
        NodeRecord("Organization", {"key": "org:tsmc-2", "name": "Taiwan Semiconductor "
                                    "Manufacturing Company Limited"},
                   (evidence(SUPPLY_CHUNK),)),
    ))
    with pytest.raises(ConstraintError):
        write_batch(written, batch)
    assert _snapshot(written) == before


def test_a_graph_without_graph_meta_is_refused(neo4j_driver, graph_test_batch):
    with pytest.raises(GraphMetaMismatch):
        write_batch(neo4j_driver, graph_test_batch)
    assert _snapshot(neo4j_driver) == ([], [])


def test_a_graph_built_under_another_ontology_is_refused(graph_driver, graph_test_batch):
    _run(graph_driver, "MATCH (m:GraphMeta) SET m.ontology_version = $v",
         v=ONTOLOGY_VERSION + 1)
    with pytest.raises(GraphMetaMismatch):
        write_batch(graph_driver, graph_test_batch)
    assert _run(graph_driver, "MATCH (n) WHERE NOT n:GraphMeta AND NOT n:`"
                f"{MARKER_LABEL}` RETURN count(n) AS n") == [{"n": 0}]


# ── merging evidence ───────────────────────────────────────────────────────────

def test_a_new_chunk_on_an_existing_edge_is_appended_with_its_span(written):
    again = Batch(edges=(
        EdgeRecord("HAS_SEGMENT", NVIDIA, COMPUTE, edge_evidence(MDA_SEGMENTS_CHUNK)),
    ))
    write_batch(written, again)

    [edge] = _edges(written, "HAS_SEGMENT")
    assert edge["p"]["chunk_ids"] == [SEGMENTS_CHUNK, MDA_SEGMENTS_CHUNK]
    assert edge["p"]["evidence_spans"] == [QUOTES[SEGMENTS_CHUNK], QUOTES[MDA_SEGMENTS_CHUNK]]

    before = _snapshot(written)
    write_batch(written, Batch(edges=(
        EdgeRecord("HAS_SEGMENT", NVIDIA, COMPUTE,
                   edge_evidence(MDA_SEGMENTS_CHUNK, SEGMENTS_CHUNK)),
    )))
    assert _snapshot(written) == before


def test_only_the_new_chunks_of_a_mixed_list_are_appended(written):
    write_batch(written, Batch(edges=(
        EdgeRecord("HAS_SEGMENT", NVIDIA, COMPUTE,
                   edge_evidence(SEGMENTS_CHUNK, MDA_SEGMENTS_CHUNK)),
    )))
    [edge] = _edges(written, "HAS_SEGMENT")
    assert edge["p"]["chunk_ids"] == [SEGMENTS_CHUNK, MDA_SEGMENTS_CHUNK]
    assert len(edge["p"]["evidence_spans"]) == 2


def test_the_same_edge_twice_in_one_batch_is_one_edge(graph_driver, graph_test_batch):
    extra = EdgeRecord("HAS_SEGMENT", NVIDIA, COMPUTE, edge_evidence(MDA_SEGMENTS_CHUNK))
    write_batch(graph_driver, replace(graph_test_batch, edges=(*graph_test_batch.edges, extra)))

    [edge] = _edges(graph_driver, "HAS_SEGMENT")
    assert edge["p"]["chunk_ids"] == [SEGMENTS_CHUNK, MDA_SEGMENTS_CHUNK]


def test_node_evidence_accumulates_through_evidenced_by_edges(written):
    metric = _metric_node()
    write_batch(written, Batch(nodes=(
        replace(metric, evidence=(evidence(MDA_CUSTOMERS_CHUNK),)),
    )))
    chunks = _run(written, "MATCH (:MetricValue {key: $key})-[e:EVIDENCED_BY]->(c:Chunk) "
                           "RETURN c.chunk_id AS chunk ORDER BY chunk",
                  key=TOP_CUSTOMER_SHARE.key_dict()["key"])
    assert [r["chunk"] for r in chunks] == [CUSTOMERS_CHUNK, MDA_CUSTOMERS_CHUNK]

    before = _snapshot(written)
    write_batch(written, Batch(nodes=(
        replace(metric, evidence=(evidence(CUSTOMERS_CHUNK), evidence(MDA_CUSTOMERS_CHUNK))),
    )))
    assert _snapshot(written) == before


def _metric_node():
    return NodeRecord("MetricValue", {
        "key": TOP_CUSTOMER_SHARE.key_dict()["key"],
        "concept": "share of total revenue from the largest direct customer",
        "value": 22, "unit": "percent", "period": "FY2026",
    }, (evidence(CUSTOMERS_CHUNK),))


def test_a_person_with_two_roles_at_one_company_has_two_edges(written):
    rows = _run(written, "MATCH (:Person {key: $key})-[r:HOLDS_ROLE_AT]->(:Company) "
                         "RETURN r.role AS role, r.chunk_ids AS chunks ORDER BY role",
                key=HUANG.key_dict()["key"])
    assert rows == [
        {"role": "Chief Executive Officer", "chunks": [OFFICERS_CHUNK]},
        {"role": "President", "chunks": [OFFICERS_CHUNK]},
    ]


def test_a_batch_may_join_nodes_and_chunks_already_in_the_graph(written):
    owns = EdgeRecord("OWNS", NVIDIA, TSMC, {"stake": 0, **edge_evidence(SUPPLY_CHUNK)})
    write_batch(written, Batch(edges=(owns,)))
    [edge] = _edges(written, "OWNS")
    assert edge["p"]["stake"] == 0.0 and isinstance(edge["p"]["stake"], float)


# ── injection ──────────────────────────────────────────────────────────────────

CYPHER = "x'}) MATCH (n) DETACH DELETE n //` RETURN 1 //"


def test_a_property_value_holding_cypher_is_stored_as_plain_data(written):
    nodes_before = len(_snapshot(written)[0])
    batch = Batch(nodes=(
        NodeRecord("Organization", {"key": CYPHER, "name": CYPHER},
                   (Evidence(SUPPLY_CHUNK, "TSMC"),)),
    ))
    write_batch(written, batch)

    rows = _run(written, "MATCH (o:Organization {key: $key}) RETURN o.name AS name", key=CYPHER)
    assert rows == [{"name": CYPHER}]
    assert len(_snapshot(written)[0]) == nodes_before + 1


def test_a_label_holding_cypher_is_rejected_before_any_query(written):
    before = _snapshot(written)
    batch = Batch(nodes=(
        NodeRecord(f"Organization`) {CYPHER}", {"key": "k", "name": "n"},
                   (Evidence(SUPPLY_CHUNK, "TSMC"),)),
    ))
    with pytest.raises(BatchRejected) as exc:
        write_batch(written, batch)
    assert [v.rule for v in exc.value.violations] == [Rule.UNKNOWN_LABEL]
    assert _snapshot(written) == before


def test_the_write_path_uses_no_dynamic_labels():
    assert "$(" not in inspect.getsource(graph.write)
