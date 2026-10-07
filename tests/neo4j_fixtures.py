"""The Neo4j test database and its guard (Step 7).

Graph tests connect only through NEO4J_TEST_URI, NEO4J_TEST_USER and
NEO4J_TEST_PASSWORD (the `neo4j-test` compose service locally, a service
container in CI), never NEO4J_URI. As a second guard, a database is used only
if it holds the test marker node, which `claim_test_database` creates in a
completely empty database and nowhere else.
"""
from __future__ import annotations

import os

import pytest
from neo4j import Driver

from graph.connection import DATABASE, driver_from_env

TEST_ENV_PREFIX = "NEO4J_TEST"
MARKER_LABEL = "FilingIntelTestMarker"


class NotATestDatabase(RuntimeError):
    """The database has data but no test marker, so it may be a real graph."""


def _quote(name: str) -> str:
    """Backtick-quote a name read back from the database, escaping backticks."""
    return "`" + name.replace("`", "``") + "`"


def _count(driver: Driver, query: str) -> int:
    records, _, _ = driver.execute_query(query, database_=DATABASE)
    return records[0]["n"]


def claim_test_database(driver: Driver) -> None:
    """Create the marker in an empty database; refuse one with data but no marker."""
    if _count(driver, f"MATCH (m:`{MARKER_LABEL}`) RETURN count(m) AS n"):
        return
    nodes = _count(driver, "MATCH (n) RETURN count(n) AS n")
    constraints = _count(driver, "SHOW CONSTRAINTS YIELD name RETURN count(name) AS n")
    if nodes or constraints:
        raise NotATestDatabase(
            f"{TEST_ENV_PREFIX}_URI points at a database with {nodes} nodes and "
            f"{constraints} constraints but no :{MARKER_LABEL} node. Graph tests "
            "wipe the database, so they only run against an empty test instance."
        )
    driver.execute_query(f"CREATE (:`{MARKER_LABEL}`)", database_=DATABASE)


def wipe_except_marker(driver: Driver) -> None:
    """Delete every node but the marker, every constraint and every non-lookup index."""
    driver.execute_query(
        f"MATCH (n) WHERE NOT n:`{MARKER_LABEL}` DETACH DELETE n", database_=DATABASE,
    )
    constraints, _, _ = driver.execute_query(
        "SHOW CONSTRAINTS YIELD name RETURN name", database_=DATABASE,
    )
    for row in constraints:
        driver.execute_query(f"DROP CONSTRAINT {_quote(row['name'])}", database_=DATABASE)
    indexes, _, _ = driver.execute_query(
        "SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP' RETURN name",
        database_=DATABASE,
    )
    for row in indexes:
        driver.execute_query(f"DROP INDEX {_quote(row['name'])}", database_=DATABASE)


@pytest.fixture(scope="session")
def neo4j_test_session():
    """A driver for the claimed test database; skips without NEO4J_TEST_URI."""
    if not os.environ.get(f"{TEST_ENV_PREFIX}_URI"):
        pytest.skip(f"{TEST_ENV_PREFIX}_URI is not set")
    driver = driver_from_env(TEST_ENV_PREFIX)
    try:
        driver.verify_connectivity()
        try:
            claim_test_database(driver)
        except NotATestDatabase as exc:
            pytest.fail(str(exc), pytrace=False)
        yield driver
    finally:
        driver.close()


@pytest.fixture
def neo4j_driver(neo4j_test_session):
    """The test database wiped down to its marker before the test."""
    wipe_except_marker(neo4j_test_session)
    return neo4j_test_session
