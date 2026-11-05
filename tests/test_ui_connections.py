"""The backend's shared Postgres and Neo4j readers (Step 14 ticket 01).

Needs ``DATABASE_URL``; the Neo4j cases use a driver that cannot connect.
"""
import psycopg
import pytest
from neo4j import GraphDatabase

from ingest.chunker import chunk_filing
from ingest.store import load_filing, resolve
from tests.conftest import FIXTURE_NAMES, throwaway_schema
from ui.connections import SharedConnections, open_backend
from ui.errors import DatabaseUnavailable, SourceNotFound


@pytest.fixture(scope="module")
def stored(fixture_records):
    with throwaway_schema() as conn:
        record = fixture_records[FIXTURE_NAMES.index("intc_10q")]
        chunks = chunk_filing(record.filing)
        load_filing(conn, record, chunks)
        yield conn, chunks


def _unreachable_driver():
    return GraphDatabase.driver("bolt://127.0.0.1:1", auth=("neo4j", "x"),
                                connection_timeout=0.5, max_transaction_retry_time=0,
                                telemetry_disabled=True)


def test_source_passages_read_through_one_shared_connection(stored):
    conn, chunks = stored
    opened = []

    def connect():
        opened.append(1)
        return conn

    shared = SharedConnections(connect, _unreachable_driver)
    chunk_id = chunks[5].chunk_id
    assert shared.source(chunk_id).chunk == resolve(conn, chunk_id)
    shared.source(chunks[6].chunk_id)
    assert opened == [1]
    with pytest.raises(SourceNotFound):
        shared.source("0000050863-99-000000:0000")


def test_an_unreachable_postgres_is_a_plain_error():
    def refuse():
        raise psycopg.OperationalError("connection to server at 10.0.0.1 failed: secret")

    shared = SharedConnections(refuse, _unreachable_driver)
    with pytest.raises(DatabaseUnavailable, match="docker compose up -d") as caught:
        shared.source("x:0001")
    assert "secret" not in str(caught.value)


def test_an_unreachable_neo4j_is_a_plain_error():
    shared = SharedConnections(lambda: None, _unreachable_driver)
    with pytest.raises(DatabaseUnavailable, match="Neo4j"):
        shared.communities(("lower-a",))


def test_open_backend_wires_the_shared_readers_and_closes_them(stored):
    backend, close = open_backend({"DATABASE_URL": "postgresql://127.0.0.1:1/none"})
    with pytest.raises(DatabaseUnavailable):
        backend.source("x:0001")
    assert backend.max_spend == 1.0
    close()
