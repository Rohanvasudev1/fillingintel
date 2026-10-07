"""Tests for validate_graph() and `python -m graph.validate` (Step 7).

Each broken case starts from the valid test graph and breaks one thing with
raw Cypher that goes around the write path, then checks that exactly that
check fails. They skip without NEO4J_TEST_URI; CI provides one. Locally:
`docker compose up -d neo4j-test`.

The test marker node is not an ontology label, so the `unmarked` fixture
removes it for each test and puts it back afterwards; validate_graph() has no
test-only exclusion.
"""
from contextlib import nullcontext

import psycopg
import pytest

from graph.connection import DATABASE
from graph.constraints import apply_constraints
from graph.validate import (
    EXAMPLE_LIMIT,
    EXIT_OK,
    EXIT_RUN_ERROR,
    EXIT_USAGE,
    EXIT_VIOLATIONS,
    Check,
    GraphValidationError,
    main,
    validate_graph,
)
from graph.write import write_batch
from ingest.chunker import chunk_filing
from ingest.store import load_filing
from tests.graph_test_data import (
    AMD_10K,
    EXPORT_CHUNK,
    NVDA_10K,
    NVDA_CIK,
    SEGMENTS_CHUNK,
)
from tests.neo4j_fixtures import MARKER_LABEL, TEST_ENV_PREFIX

UNKNOWN_CHUNK = f"{NVDA_10K}:9999"


def _run(driver, query, **params):
    driver.execute_query(query, params, database_=DATABASE)


@pytest.fixture
def unmarked(neo4j_driver):
    """The wiped test database without its marker; the marker is restored afterwards."""
    _run(neo4j_driver, f"MATCH (m:`{MARKER_LABEL}`) DELETE m")
    yield neo4j_driver
    _run(neo4j_driver, f"MERGE (:`{MARKER_LABEL}`)")


@pytest.fixture
def empty_graph(unmarked):
    """No nodes but :GraphMeta, with the constraints applied."""
    apply_constraints(unmarked)
    return unmarked


@pytest.fixture
def valid_graph(empty_graph, graph_test_batch):
    """The valid test graph, written through the write path."""
    write_batch(empty_graph, graph_test_batch)
    return empty_graph


@pytest.fixture(scope="module")
def postgres(db_conn, fixture_records):
    """A throwaway Postgres schema holding the two filings the test graph uses."""
    for record in fixture_records:
        if record.meta.accession_no in (NVDA_10K, AMD_10K):
            load_filing(db_conn, record, chunk_filing(record.filing))
    return db_conn


def _failures(driver, postgres=None):
    """{check: count} for the checks that failed; {} when the graph is valid."""
    try:
        validate_graph(driver, postgres)
    except GraphValidationError as exc:
        return {r.check: r.count for r in exc.report.results if r.count}
    return {}


# ── valid graphs ───────────────────────────────────────────────────────────────

def test_the_valid_test_graph_passes(valid_graph):
    report = validate_graph(valid_graph)

    assert report.ok
    assert not report.postgres_checked


def test_an_empty_graph_with_graph_meta_passes(empty_graph):
    assert validate_graph(empty_graph).ok


def test_the_report_lists_every_check_it_ran(valid_graph):
    report = validate_graph(valid_graph)

    assert [r.check for r in report.results] == [c for c in Check if c is not Check.POSTGRES]
    assert "Postgres check: skipped" in str(report)


def test_the_valid_test_graph_passes_the_postgres_check(valid_graph, postgres):
    report = validate_graph(valid_graph, postgres)

    assert report.ok
    assert report.postgres_checked
    assert Check.POSTGRES in [r.check for r in report.results]


# ── one broken thing each ──────────────────────────────────────────────────────

BROKEN = {
    "unknown label": (
        "CREATE (:Scratch {key: 'scratch'})", Check.NODE_LABELS,
    ),
    "two ontology labels on one node": (
        "MATCH (o:Organization) SET o:Product", Check.NODE_LABELS,
    ),
    "unknown edge type": (
        "MATCH (a:Company {cik: $cik}), (o:Organization) CREATE (a)-[:PARTNERS_WITH]->(o)",
        Check.EDGE_TYPES,
    ),
    "disallowed endpoint": (
        "MATCH (p:Period {cik: $cik}), (o:Organization) "
        "CREATE (p)-[:SUPPLIES {chunk_ids: [$chunk], evidence_spans: ['TSMC'], "
        "ontology_version: 1}]->(o)",
        Check.ENDPOINTS,
    ),
    "missing property": (
        "MATCH (o:Organization) REMOVE o.name", Check.NODE_PROPERTIES,
    ),
    "wrong property type": (
        "MATCH (m:MetricValue) SET m.value = '22'", Check.NODE_PROPERTIES,
    ),
    "missing edge property": (
        "MATCH (:Person)-[r:HOLDS_ROLE_AT {role: 'President'}]->() REMOVE r.role",
        Check.EDGE_PROPERTIES,
    ),
    "extracted node without evidence": (
        "MATCH (:Regulation)-[e:EVIDENCED_BY]->() DELETE e", Check.NODE_EVIDENCE,
    ),
    "empty chunk_ids": (
        "MATCH ()-[r:SUPPLIES]->() SET r.chunk_ids = [], r.evidence_spans = []",
        Check.EDGE_EVIDENCE,
    ),
    "dangling chunk ID": (
        "MATCH ()-[r:SUPPLIES]->() SET r.chunk_ids = [$unknown]", Check.EDGE_EVIDENCE,
    ),
    "spans and chunks of different lengths": (
        "MATCH ()-[r:SUPPLIES]->() SET r.evidence_spans = r.evidence_spans + ['TSMC']",
        Check.EDGE_EVIDENCE,
    ),
    "Filing without FILED": (
        "MATCH ()-[r:FILED]->(:Filing {accession_no: $amd}) DELETE r", Check.STRUCTURE,
    ),
    "Filing without COVERS_PERIOD": (
        "MATCH (:Filing {accession_no: $amd})-[r:COVERS_PERIOD]->() DELETE r", Check.STRUCTURE,
    ),
    "Chunk without PART_OF": (
        "MATCH (:Chunk {chunk_id: $chunk})-[r:PART_OF]->() DELETE r", Check.STRUCTURE,
    ),
    "Chunk PART_OF another filing": (
        "MATCH (c:Chunk {chunk_id: $chunk})-[r:PART_OF]->() DELETE r "
        "WITH c MATCH (f:Filing {accession_no: $amd}) "
        "CREATE (c)-[:PART_OF {ontology_version: 1}]->(f)",
        Check.STRUCTURE,
    ),
    "wrong GraphMeta": (
        "MATCH (m:GraphMeta) SET m.ontology_version = 0", Check.GRAPH_META,
    ),
    "missing GraphMeta": (
        "MATCH (m:GraphMeta) DELETE m", Check.GRAPH_META,
    ),
}


@pytest.mark.parametrize("query, check", BROKEN.values(), ids=BROKEN.keys())
def test_each_broken_case_fails_exactly_its_check(valid_graph, query, check):
    _run(valid_graph, query, cik=NVDA_CIK, chunk=SEGMENTS_CHUNK, unknown=UNKNOWN_CHUNK,
         amd=AMD_10K)

    assert _failures(valid_graph) == {check: 1}


def test_a_chunk_unknown_to_postgres_fails_the_postgres_check(valid_graph, postgres):
    _run(valid_graph,
         "MATCH (f:Filing {accession_no: $nvda}) "
         "CREATE (:Chunk {chunk_id: $unknown, accession_no: $nvda, section: 'item_1', "
         "ontology_version: 1})-[:PART_OF {ontology_version: 1}]->(f)",
         nvda=NVDA_10K, unknown=UNKNOWN_CHUNK)

    assert _failures(valid_graph) == {}
    assert _failures(valid_graph, postgres) == {Check.POSTGRES: 1}


# Moves a Chunk node and its PART_OF edge to the AMD filing, so the graph stays
# self-consistent and only Postgres can tell.
MOVE_CHUNK_TO_AMD = (
    "MATCH (c:Chunk {chunk_id: $chunk})-[r:PART_OF]->() DELETE r "
    "WITH c MATCH (f:Filing {accession_no: $amd}) SET c.accession_no = $amd "
    "CREATE (c)-[:PART_OF {ontology_version: 1}]->(f)"
)


def test_a_chunk_under_another_filing_fails_the_postgres_check(valid_graph, postgres):
    _run(valid_graph, MOVE_CHUNK_TO_AMD, chunk=EXPORT_CHUNK, amd=AMD_10K)

    assert _failures(valid_graph) == {}
    assert _failures(valid_graph, postgres) == {Check.POSTGRES: 1}


# ── reporting ──────────────────────────────────────────────────────────────────

def test_every_kind_of_violation_is_reported_with_capped_examples(valid_graph):
    _run(valid_graph, "UNWIND range(1, 25) AS i CREATE (:Scratch {key: 'scratch-' + i})")
    _run(valid_graph, "MATCH (o:Organization) REMOVE o.name")
    _run(valid_graph, "MATCH (m:GraphMeta) SET m.schema_sha256 = 'old'")

    with pytest.raises(GraphValidationError) as exc:
        validate_graph(valid_graph)

    report = exc.value.report
    failed = {r.check: r for r in report.results if r.count}
    assert {c: r.count for c, r in failed.items()} == {
        Check.NODE_LABELS: 25, Check.NODE_PROPERTIES: 1, Check.GRAPH_META: 1,
    }
    assert len(failed[Check.NODE_LABELS].examples) == EXAMPLE_LIMIT
    assert len(failed[Check.NODE_PROPERTIES].examples) == 1
    assert "27 violations" in str(exc.value)


def test_examples_name_the_bad_item(valid_graph):
    _run(valid_graph, "MATCH (o:Organization) REMOVE o.name")

    with pytest.raises(GraphValidationError) as exc:
        validate_graph(valid_graph)

    (result,) = [r for r in exc.value.report.results if r.count]
    assert "org:tsmc" in result.examples[0]
    assert "name" in result.examples[0]


# ── the command ────────────────────────────────────────────────────────────────

@pytest.fixture
def graph_env(monkeypatch):
    """NEO4J_* pointing at the test database, with DATABASE_URL unset."""
    import os

    for suffix in ("URI", "USER", "PASSWORD"):
        monkeypatch.setenv(f"NEO4J_{suffix}", os.environ[f"{TEST_ENV_PREFIX}_{suffix}"])
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return monkeypatch


def test_command_exits_0_on_a_valid_graph(valid_graph, graph_env, capsys):
    assert main([]) == EXIT_OK

    out = capsys.readouterr().out
    assert all(c.value in out for c in Check if c is not Check.POSTGRES)
    assert "Postgres check: skipped" in out


def test_command_exits_4_on_violations(valid_graph, graph_env, capsys):
    _run(valid_graph, "CREATE (:Scratch)")

    assert main([]) == EXIT_VIOLATIONS
    assert Check.NODE_LABELS.value in capsys.readouterr().out


def test_command_runs_the_postgres_check_when_database_url_is_set(
    valid_graph, graph_env, postgres, capsys,
):
    graph_env.setenv("DATABASE_URL", "postgresql://unused")

    assert main([], connect_postgres=lambda url: nullcontext(postgres)) == EXIT_OK
    assert "Postgres check: ran" in capsys.readouterr().out


def test_command_exits_4_when_the_postgres_check_fails(valid_graph, graph_env, postgres):
    graph_env.setenv("DATABASE_URL", "postgresql://unused")
    _run(valid_graph, MOVE_CHUNK_TO_AMD, chunk=EXPORT_CHUNK, amd=AMD_10K)

    assert main([], connect_postgres=lambda url: nullcontext(postgres)) == EXIT_VIOLATIONS


def test_command_exits_2_on_bad_arguments():
    with pytest.raises(SystemExit) as exc:
        main(["--no-such-option"])
    assert exc.value.code == EXIT_USAGE


def test_command_exits_3_without_settings(monkeypatch, capsys):
    monkeypatch.delenv("NEO4J_URI", raising=False)

    assert main([]) == EXIT_RUN_ERROR
    assert "NEO4J_URI" in capsys.readouterr().err


def test_command_exits_3_when_neo4j_is_unreachable(monkeypatch, capsys):
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "unused")

    assert main([]) == EXIT_RUN_ERROR
    assert "could not run" in capsys.readouterr().err


def test_command_exits_3_when_postgres_is_unreachable(valid_graph, graph_env, capsys):
    graph_env.setenv("DATABASE_URL", "postgresql://unused")

    def refuse(url):
        raise psycopg.OperationalError("connection refused")

    assert main([], connect_postgres=refuse) == EXIT_RUN_ERROR
    assert "could not run" in capsys.readouterr().err


def test_the_command_opens_postgres_read_only():
    import os

    from graph.validate import _connect_read_only

    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL is not set")
    with _connect_read_only(os.environ["DATABASE_URL"]) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TEMPORARY TABLE scratch (id int)")
