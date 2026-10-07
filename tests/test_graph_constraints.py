"""Tests for apply_constraints() and the Neo4j test database guard (Step 7).

The live tests run against the Neo4j test instance (NEO4J_TEST_URI) and skip
without it; CI provides one. Locally: `docker compose up -d neo4j-test`.
"""
import os

import pytest
from neo4j.exceptions import ConstraintError

from graph.connection import DATABASE, GraphSettingsMissing, driver_from_env
from graph.constraints import ConstraintMismatch, GraphMetaMismatch, apply_constraints
from graph.ontology import ONTOLOGY_VERSION
from graph.schema_text import SCHEMA_TEXT_SHA256
from tests.neo4j_fixtures import (
    MARKER_LABEL,
    NotATestDatabase,
    claim_test_database,
    wipe_except_marker,
)

EXTRACTED_LABELS = [
    "Organization", "Segment", "Product", "RiskFactor", "Regulation",
    "LegalProceeding", "Agreement", "Facility", "Person", "MetricValue",
]

# name -> (label, properties); every one a node uniqueness constraint.
EXPECTED_CONSTRAINTS = {
    "company_cik_unique": ("Company", ["cik"]),
    "filing_accession_no_unique": ("Filing", ["accession_no"]),
    "period_cik_fiscal_period_unique": ("Period", ["cik", "fiscal_period"]),
    "chunk_chunk_id_unique": ("Chunk", ["chunk_id"]),
    "organization_key_unique": ("Organization", ["key"]),
    "segment_key_unique": ("Segment", ["key"]),
    "product_key_unique": ("Product", ["key"]),
    "risk_factor_key_unique": ("RiskFactor", ["key"]),
    "regulation_key_unique": ("Regulation", ["key"]),
    "legal_proceeding_key_unique": ("LegalProceeding", ["key"]),
    "agreement_key_unique": ("Agreement", ["key"]),
    "facility_key_unique": ("Facility", ["key"]),
    "person_key_unique": ("Person", ["key"]),
    "metric_value_key_unique": ("MetricValue", ["key"]),
}


def _run(driver, query, **params):
    records, _, _ = driver.execute_query(query, params, database_=DATABASE)
    return [r.data() for r in records]


def _constraints(driver):
    return {
        r["name"]: r
        for r in _run(
            driver,
            "SHOW CONSTRAINTS YIELD id, name, type, entityType, labelsOrTypes, properties "
            "RETURN id, name, type, entityType, labelsOrTypes, properties",
        )
    }


def _graph_meta(driver):
    return _run(
        driver,
        "MATCH (m:GraphMeta) RETURN elementId(m) AS id, properties(m) AS props",
    )


def _non_marker_node_count(driver):
    return _run(driver, f"MATCH (n) WHERE NOT n:`{MARKER_LABEL}` RETURN count(n) AS n")[0]["n"]


def test_ci_always_has_a_neo4j_test_database():
    """The graph tests skip without NEO4J_TEST_URI; CI must never skip them."""
    if os.environ.get("CI"):
        assert os.environ.get("NEO4J_TEST_URI"), "CI must set NEO4J_TEST_URI"


def test_driver_from_env_names_every_missing_setting(monkeypatch):
    for name in ("NEO4J_TEST_URI", "NEO4J_TEST_USER", "NEO4J_TEST_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(GraphSettingsMissing) as exc:
        driver_from_env("NEO4J_TEST")
    for name in ("NEO4J_TEST_URI", "NEO4J_TEST_USER", "NEO4J_TEST_PASSWORD"):
        assert name in str(exc.value)


# ── constraints ────────────────────────────────────────────────────────────────

def test_every_ontology_key_has_a_uniqueness_constraint(neo4j_driver):
    apply_constraints(neo4j_driver)

    found = {
        name: (row["labelsOrTypes"][0], row["properties"])
        for name, row in _constraints(neo4j_driver).items()
    }
    assert found == EXPECTED_CONSTRAINTS
    for row in _constraints(neo4j_driver).values():
        assert (row["type"], row["entityType"]) == ("UNIQUENESS", "NODE")
    assert {label for label, _ in found.values()} >= set(EXTRACTED_LABELS)


def test_apply_constraints_writes_one_graph_meta(neo4j_driver):
    apply_constraints(neo4j_driver)

    [meta] = _graph_meta(neo4j_driver)
    assert meta["props"] == {
        "ontology_version": ONTOLOGY_VERSION, "schema_sha256": SCHEMA_TEXT_SHA256,
    }


def test_apply_constraints_twice_changes_nothing(neo4j_driver):
    apply_constraints(neo4j_driver)
    constraints, meta = _constraints(neo4j_driver), _graph_meta(neo4j_driver)

    apply_constraints(neo4j_driver)

    assert _constraints(neo4j_driver) == constraints  # same ids: nothing recreated
    assert _graph_meta(neo4j_driver) == meta


def test_ontology_constraint_name_on_another_property_is_a_mismatch(neo4j_driver):
    # IF NOT EXISTS keeps this old definition silently; the read-back must catch it.
    _run(neo4j_driver,
         "CREATE CONSTRAINT company_cik_unique FOR (c:Company) REQUIRE c.ticker IS UNIQUE")

    with pytest.raises(ConstraintMismatch) as exc:
        apply_constraints(neo4j_driver)

    assert [d.name for d in exc.value.different] == ["company_cik_unique"]
    assert exc.value.missing == () and exc.value.extra == ()
    assert "company_cik_unique" in str(exc.value)
    assert _graph_meta(neo4j_driver) == []


def test_extra_constraint_on_an_ontology_label_is_a_mismatch(neo4j_driver):
    _run(neo4j_driver,
         "CREATE CONSTRAINT company_name_unique FOR (c:Company) REQUIRE c.name IS UNIQUE")

    with pytest.raises(ConstraintMismatch) as exc:
        apply_constraints(neo4j_driver)

    assert [e.name for e in exc.value.extra] == ["company_name_unique"]
    assert exc.value.missing == () and exc.value.different == ()
    assert _graph_meta(neo4j_driver) == []


def test_constraint_on_graph_meta_is_a_mismatch(neo4j_driver):
    _run(neo4j_driver,
         "CREATE CONSTRAINT graph_meta_unique FOR (m:GraphMeta) REQUIRE m.ontology_version "
         "IS UNIQUE")

    with pytest.raises(ConstraintMismatch) as exc:
        apply_constraints(neo4j_driver)

    assert [e.name for e in exc.value.extra] == ["graph_meta_unique"]


def test_constraint_on_another_label_is_left_alone(neo4j_driver):
    _run(neo4j_driver,
         "CREATE CONSTRAINT scratch_id_unique FOR (s:Scratch) REQUIRE s.id IS UNIQUE")

    apply_constraints(neo4j_driver)

    assert "scratch_id_unique" in _constraints(neo4j_driver)


def test_same_schema_under_another_name_is_reported_missing_and_extra(neo4j_driver):
    # IF NOT EXISTS also skips a constraint whose schema exists under another name.
    _run(neo4j_driver,
         "CREATE CONSTRAINT old_company_cik FOR (c:Company) REQUIRE c.cik IS UNIQUE")

    with pytest.raises(ConstraintMismatch) as exc:
        apply_constraints(neo4j_driver)

    assert [m.name for m in exc.value.missing] == ["company_cik_unique"]
    assert [e.name for e in exc.value.extra] == ["old_company_cik"]


@pytest.mark.parametrize(
    ("version", "sha256"),
    [(ONTOLOGY_VERSION + 1, SCHEMA_TEXT_SHA256), (ONTOLOGY_VERSION, "0" * 64)],
    ids=["other-version", "other-hash"],
)
def test_graph_meta_from_another_ontology_is_refused_unchanged(neo4j_driver, version, sha256):
    _run(neo4j_driver,
         "CREATE (:GraphMeta {ontology_version: $v, schema_sha256: $h})", v=version, h=sha256)
    before = _graph_meta(neo4j_driver)

    with pytest.raises(GraphMetaMismatch):
        apply_constraints(neo4j_driver)

    assert _graph_meta(neo4j_driver) == before
    assert _constraints(neo4j_driver) == {}


def test_two_graph_meta_nodes_are_refused(neo4j_driver):
    for _ in range(2):
        _run(neo4j_driver,
             "CREATE (:GraphMeta {ontology_version: $v, schema_sha256: $h})",
             v=ONTOLOGY_VERSION, h=SCHEMA_TEXT_SHA256)

    with pytest.raises(GraphMetaMismatch):
        apply_constraints(neo4j_driver)


@pytest.mark.parametrize(
    "create",
    [
        "CREATE (:Organization {key: 'tsmc', name: 'TSMC'})",
        "CREATE (:Period {cik: '1045810', fiscal_period: 'FY2026'})",
        "CREATE (:Chunk {chunk_id: '0001045810-26-000021:0001'})",
    ],
    ids=["extracted-key", "composite-period", "chunk-id"],
)
def test_duplicate_key_violates_the_uniqueness_constraint(neo4j_driver, create):
    apply_constraints(neo4j_driver)
    _run(neo4j_driver, create)

    with pytest.raises(ConstraintError):
        _run(neo4j_driver, create)


def test_same_period_label_at_two_filers_is_allowed(neo4j_driver):
    apply_constraints(neo4j_driver)
    _run(neo4j_driver, "CREATE (:Period {cik: '1045810', fiscal_period: 'FY2026'})")
    _run(neo4j_driver, "CREATE (:Period {cik: '2488', fiscal_period: 'FY2026'})")

    assert _run(neo4j_driver, "MATCH (p:Period) RETURN count(p) AS n")[0]["n"] == 2


# ── the test-database guard ──────────────────────────────────────────────────────

def _marker_count(driver):
    return _run(driver, f"MATCH (m:`{MARKER_LABEL}`) RETURN count(m) AS n")[0]["n"]


@pytest.fixture
def unclaimed(neo4j_driver):
    """The test database with its marker removed; claimed again afterwards."""
    _run(neo4j_driver, f"MATCH (m:`{MARKER_LABEL}`) DELETE m")
    yield neo4j_driver
    wipe_except_marker(neo4j_driver)
    _run(neo4j_driver, "MATCH (n) DETACH DELETE n")
    _run(neo4j_driver, f"CREATE (:`{MARKER_LABEL}`)")


def test_wipe_keeps_only_the_marker(neo4j_driver):
    apply_constraints(neo4j_driver)
    _run(neo4j_driver, "CREATE (:Organization {key: 'tsmc'})-[:X]->(:Scratch)")

    wipe_except_marker(neo4j_driver)

    assert _non_marker_node_count(neo4j_driver) == 0
    assert _constraints(neo4j_driver) == {}
    assert _marker_count(neo4j_driver) == 1


def test_claim_refuses_nodes_without_a_marker(unclaimed):
    _run(unclaimed, "CREATE (:Company {cik: '1045810'})")

    with pytest.raises(NotATestDatabase):
        claim_test_database(unclaimed)
    assert _marker_count(unclaimed) == 0


def test_claim_refuses_constraints_without_a_marker(unclaimed):
    # A real graph with constraints applied but nothing loaded yet.
    _run(unclaimed, "CREATE CONSTRAINT scratch_id_unique FOR (s:Scratch) REQUIRE s.id IS UNIQUE")

    with pytest.raises(NotATestDatabase):
        claim_test_database(unclaimed)
    assert _marker_count(unclaimed) == 0


def test_claim_marks_an_empty_database(unclaimed):
    claim_test_database(unclaimed)

    assert _marker_count(unclaimed) == 1
