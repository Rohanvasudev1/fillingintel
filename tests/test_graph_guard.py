"""Tests for the Cypher guard and executor on the test database (Step 11, ticket 04).

Each bad query breaks exactly one rule, as in the validator tests. The text
and ontology rules have their own offline tests (test_cypher_static_check.py);
here they run through run_guarded() to show that a query they stop never
reaches the server. Skip without NEO4J_TEST_URI; CI provides one.

A cold server can take seconds over its first queries, so the tests pass a
generous timeout (SLACK_S) except where the timeout is what they test.
"""
from functools import partial

import pytest

from graph.connection import DATABASE
from graph.guard import (
    CHUNK_IDS,
    MAX_ROWS,
    Category,
    GuardFailure,
    GuardPass,
    Rule,
)
from graph.guard import execute as execute_with_timeout
from graph.guard import explain_check as explain_with_timeout
from graph.guard import run_guarded as run_with_timeout
from tests.graph_test_data import NVDA_10K, NVDA_CIK

SLACK_S = 60.0
run_guarded = partial(run_with_timeout, timeout=SLACK_S)
explain_check = partial(explain_with_timeout, timeout=SLACK_S)
execute = partial(execute_with_timeout, timeout=SLACK_S)

CHUNK = f"{NVDA_10K}:0001"
EVIDENCE = (
    "MATCH (co:Company)-[:FILED]->(f:Filing)<-[:PART_OF]-(c:Chunk) "
    "RETURN co.ticker AS ticker, collect(c.chunk_id) AS chunk_ids"
)
# Counts to a billion before returning, so a short timeout ends it.
SLOW = ("UNWIND range(1, 1000000000) AS x WITH x WHERE x % 7 = 123456 "
        "RETURN collect(toString(x)) AS chunk_ids")


@pytest.fixture
def graph(neo4j_driver):
    """One filer, filing and chunk, so the names the queries use exist in the database."""
    neo4j_driver.execute_query(
        "CREATE (:Company {cik: $cik, ticker: 'NVDA'})-[:FILED]->"
        "(f:Filing {accession_no: $acc})<-[:PART_OF]-(:Chunk {chunk_id: $chunk})",
        cik=NVDA_CIK, acc=NVDA_10K, chunk=CHUNK, database_=DATABASE,
    )
    return neo4j_driver


def _scratch_count(driver) -> int:
    records, _, _ = driver.execute_query("MATCH (n:Scratch) RETURN count(n) AS n",
                                         database_=DATABASE)
    return records[0]["n"]


def _failure(result, category: Category, rule: Rule) -> GuardFailure:
    assert isinstance(result, GuardFailure), result
    assert (result.category, result.rule) == (category, rule), result.message
    return result


def test_a_valid_query_returns_its_rows(graph):
    assert run_guarded(graph, EVIDENCE) == GuardPass(
        ({"ticker": "NVDA", CHUNK_IDS: [CHUNK]},), truncated=False)


def test_a_final_semicolon_is_allowed(graph):
    assert isinstance(run_guarded(graph, f"{EVIDENCE};"), GuardPass)


@pytest.mark.parametrize(("cypher", "rule"), [
    ("LOAD CSV FROM 'http://127.0.0.1:9/x.csv' AS row RETURN [] AS chunk_ids", Rule.TEXT),
    ("TERMINATE TRANSACTIONS 'neo4j-transaction-1'", Rule.TEXT),
    ("SHOW TRANSACTIONS YIELD transactionId RETURN [transactionId] AS chunk_ids", Rule.TEXT),
    ("CREATE (n:Scratch) RETURN [] AS chunk_ids", Rule.TEXT),
    ("CALL db.labels() YIELD label RETURN [label] AS chunk_ids", Rule.TEXT),
    (f"{EVIDENCE}; CREATE (n:Scratch)", Rule.TEXT),
    ("MATCH (c:Compny) RETURN [] AS chunk_ids", Rule.ONTOLOGY),
    ("MATCH (c:Company)-[:FILLED]->(f:Filing) RETURN [] AS chunk_ids", Rule.ONTOLOGY),
    ("MATCH (c:Company) RETURN c.revenue AS r, [] AS chunk_ids", Rule.ONTOLOGY),
    ("MATCH (f:Filing)-[:FILED]->(c:Company) RETURN [] AS chunk_ids", Rule.ONTOLOGY),
], ids=["load-csv", "terminate", "show", "write", "procedure", "two-statements",
        "unknown-label", "unknown-type", "unknown-property", "reversed-edge"])
def test_static_rules_stop_a_query_before_the_server(graph, cypher, rule):
    _failure(run_guarded(graph, cypher), Category.INVALID, rule)
    assert _scratch_count(graph) == 0


def test_a_syntax_error_is_invalid_at_explain(graph):
    failure = _failure(run_guarded(graph, "MATCH (c:Company RETURN [] AS chunk_ids"),
                       Category.INVALID, Rule.EXPLAIN)
    assert "SyntaxError" in failure.message


def test_explain_refuses_a_write_plan(graph):
    failure = _failure(explain_check(graph, "CREATE (n:Scratch) RETURN [] AS chunk_ids"),
                       Category.INVALID, Rule.EXPLAIN)
    assert "'rw'" in failure.message
    assert _scratch_count(graph) == 0


def test_explain_reports_an_unknown_label(graph):
    failure = _failure(explain_check(graph, "MATCH (n:NotInTheGraph) RETURN [] AS chunk_ids"),
                       Category.INVALID, Rule.EXPLAIN)
    assert "01N50" in failure.message


def test_read_mode_refuses_a_write_with_the_text_rules_bypassed(graph):
    failure = _failure(execute(graph, "CREATE (n:Scratch) RETURN [] AS chunk_ids"),
                       Category.INVALID, Rule.EXECUTE)
    assert "AccessMode" in failure.message
    assert _scratch_count(graph) == 0


def test_a_missing_chunk_ids_column_is_invalid(graph):
    failure = _failure(run_guarded(graph, "MATCH (c:Company) RETURN c.ticker AS ticker"),
                       Category.INVALID, Rule.RESULT)
    assert CHUNK_IDS in failure.message


def test_chunk_ids_must_hold_strings(graph):
    _failure(run_guarded(graph, "MATCH (c:Company) RETURN [1, 2] AS chunk_ids"),
             Category.INVALID, Rule.RESULT)


def test_a_server_error_is_categorised(graph):
    failure = _failure(run_guarded(graph, "MATCH (c:Company) RETURN 1 / 0 AS n, [] AS chunk_ids"),
                       Category.ERROR, Rule.EXECUTE)
    assert "ArithmeticError" in failure.message


def test_a_slow_query_times_out(graph):
    _failure(execute_with_timeout(graph, SLOW, timeout=1.0), Category.TIMEOUT, Rule.EXECUTE)


def test_reading_stops_at_the_row_cap(graph):
    result = run_guarded(graph, f"UNWIND range(1, {MAX_ROWS + 500}) AS x RETURN [] AS chunk_ids")
    assert isinstance(result, GuardPass)
    assert (len(result.rows), result.truncated) == (MAX_ROWS, True)
