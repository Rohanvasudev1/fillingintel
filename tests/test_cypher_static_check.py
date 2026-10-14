"""Tests for the guard's text and ontology rules (Step 11, checks 1 and 2).

These run without a database: static_check() reads only the query text and the
code ontology. Each bad query breaks exactly one rule. The committed Step 10
multi-hop queries are the valid cases: hand-written, real and multi-line.
"""
import pytest

from graph.guard import Category, Rule, static_check
from graph.multihop import QUERY_DIR, query_files

OK = "MATCH (c:Company)-[:FILED]->(f:Filing) RETURN [] AS chunk_ids"


def _fails(cypher: str, rule: Rule, *words: str) -> None:
    failure = static_check(cypher)
    assert failure is not None, cypher
    assert (failure.category, failure.rule) == (Category.INVALID, rule)
    for word in words:
        assert word in failure.message


@pytest.mark.parametrize("qid", ["q0111", "q0120", "q0127"])
def test_the_multihop_queries_pass(qid):
    assert static_check(query_files(QUERY_DIR)[qid].read_text()) is None


@pytest.mark.parametrize("cypher", [
    OK,
    "MATCH (c:Company) CALL (c) { MATCH (c)-[:FILED]->(f) RETURN f } RETURN [] AS chunk_ids",
    "MATCH (c:Company) CALL { MATCH (f:Filing) RETURN f } RETURN [] AS chunk_ids",
    "MATCH (o:Organization)-[:COMPETES_WITH]-(c:Company) RETURN [] AS chunk_ids",
    "MATCH (c:Company)<-[:SUPPLIES]-(o:Organization) RETURN [] AS chunk_ids",
    "MATCH (c:Company) WHERE c.name = 'CREATE; DELETE' RETURN [] AS chunk_ids",
    "MATCH (c:Company) // SET c.x = 1; DELETE c\nRETURN [] AS chunk_ids",
    "MATCH (c:Company) /* LOAD CSV */ RETURN {set: 1, use: c.name} AS m, [] AS chunk_ids",
    "MATCH (f:Filing) RETURN f.filing_date.year AS y, [] AS chunk_ids",
    "MATCH (c:`Company`)-[:`FILED`]->(f) RETURN [] AS chunk_ids",
    "MATCH (c:Company)-[:FILED*1..2]-(x:Chunk) RETURN [] AS chunk_ids",
    "MATCH (m:MetricValue) RETURN toString(m.value) AS v, [] AS chunk_ids;",
], ids=["plain", "scoped-subquery", "subquery", "undirected", "reversed-arrow-legal",
        "string-literal", "line-comment", "block-comment-and-map", "temporal-accessor",
        "backticks", "variable-length", "trailing-semicolon"])
def test_valid_reads_pass(cypher):
    assert static_check(cypher) is None


@pytest.mark.parametrize(("cypher", "word"), [
    ("LOAD CSV FROM 'https://example.com/x.csv' AS row RETURN [] AS chunk_ids", "LOAD CSV"),
    ("TERMINATE TRANSACTIONS 'neo4j-transaction-1'", "TERMINATE"),
    ("SHOW TRANSACTIONS", "SHOW"),
    ("USE neo4j MATCH (c:Company) RETURN [] AS chunk_ids", "USE"),
    ("CREATE (c:Company) RETURN [] AS chunk_ids", "CREATE"),
    ("MERGE (c:Company {cik: '1'}) RETURN [] AS chunk_ids", "MERGE"),
    ("MATCH (c:Company) SET c.name = 'x' RETURN [] AS chunk_ids", "SET"),
    ("MATCH (c:Company) DETACH DELETE c", "DELETE"),
    ("MATCH (c:Company) REMOVE c.name RETURN [] AS chunk_ids", "REMOVE"),
    ("DROP CONSTRAINT company_cik", "DROP"),
    ("MATCH (c:Company) FOREACH (x IN [1] | SET c.name = 'x')", "FOREACH"),
    ("MATCH (c:Company) CALL { CREATE (:Filing) } RETURN [] AS chunk_ids", "CREATE"),
    ("CALL db.labels() YIELD label RETURN [label] AS chunk_ids", "CALL"),
    ("call dbms.listConfig() YIELD name RETURN [name] AS chunk_ids", "CALL"),
    ("MATCH (c:Company) RETURN [] AS chunk_ids; MATCH (f:Filing) RETURN [] AS chunk_ids",
     "one statement"),
], ids=["load-csv", "terminate", "show", "use", "create", "merge", "set", "delete", "remove",
        "drop", "foreach", "write-in-subquery", "procedure-call", "lowercase-call",
        "two-statements"])
def test_text_rules(cypher, word):
    _fails(cypher, Rule.TEXT, word)


@pytest.mark.parametrize(("cypher", "name"), [
    ("MATCH (c:Compny) RETURN [] AS chunk_ids", "Compny"),
    ("MATCH (c:Company)-[:FILLED]->(f:Filing) RETURN [] AS chunk_ids", "FILLED"),
    ("MATCH (c:Company) RETURN c.revenue AS r, [] AS chunk_ids", "revenue"),
    ("MATCH (c:Company {symbol: 'NVDA'}) RETURN [] AS chunk_ids", "symbol"),
    ("MATCH (c) WHERE c:Supplier RETURN [] AS chunk_ids", "Supplier"),
], ids=["label", "edge-type", "property", "pattern-property", "label-predicate"])
def test_unknown_names(cypher, name):
    _fails(cypher, Rule.ONTOLOGY, name)


@pytest.mark.parametrize("cypher", [
    "MATCH (f:Filing)-[:FILED]->(c:Company) RETURN [] AS chunk_ids",
    "MATCH (c:Company)<-[:FILED]-(f:Filing) RETURN [] AS chunk_ids",
    "MATCH (c:Company)-[:SUPPLIES]->(f:Filing) RETURN [] AS chunk_ids",
    "MATCH (c:Company)-[:FILED]-(p:Period) RETURN [] AS chunk_ids",
], ids=["reversed", "reversed-left-arrow", "wrong-end", "undirected-neither-way"])
def test_disallowed_endpoints(cypher):
    _fails(cypher, Rule.ONTOLOGY, "FILED" if "FILED" in cypher else "SUPPLIES")


def test_an_unlabelled_end_is_not_checked():
    assert static_check("MATCH (f:Filing)-[:FILED]->(c) RETURN [] AS chunk_ids") is None
