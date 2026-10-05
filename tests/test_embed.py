"""Tests for ``python -m ingest.embed``: chunk embeddings in Postgres (Step 5, ticket 02).

The real ``VoyageClient`` runs over an ``httpx.MockTransport`` that replays the
response in ``tests/fixtures/voyage/``, so no test touches the network.  The
database tests use the throwaway schema from ``db_conn`` in conftest.py.
"""
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from ingest.chunker import chunk_filing
from ingest.embed import EXIT_USAGE, ChunkTooLong, embed_chunks, main
from ingest.store import SchemaMismatch, apply_schema, load_filing, resolve
from ingest.voyage import VOYAGE_URL, MissingApiKey, VoyageClient, VoyageError

RECORDED = Path(__file__).parent / "fixtures" / "voyage" / "document_response.json"
MODEL = "voyage-4-large"
FAKE_KEY = "pa-test-key-not-real-0123456789"
RECORDED_TOKENS = json.loads(RECORDED.read_text())["usage"]["total_tokens"]


class Replay:
    """An httpx transport handler that answers every request with the recorded response."""

    def __init__(self, body: dict | None = None):
        self.body = body if body is not None else json.loads(RECORDED.read_text())
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.body)


def _client(handler) -> VoyageClient:
    return VoyageClient(
        api_key=FAKE_KEY,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )


@pytest.fixture
def loaded(db_conn, fixture_records):
    """Two fixture filings loaded, embeddings cleared; returns their chunks."""
    db_conn.execute("DELETE FROM chunk_embeddings")
    db_conn.commit()
    chunks = []
    for record in fixture_records[:2]:
        filing_chunks = chunk_filing(record.filing)
        load_filing(db_conn, record, filing_chunks)
        chunks.extend(filing_chunks)
    return chunks


def _rows(conn):
    return conn.execute(
        "SELECT chunk_id, model, dimensions, text_sha256, api_token_count,"
        " vector_dims(embedding) FROM chunk_embeddings"
    ).fetchall()


def test_every_chunk_gets_one_row_with_its_text_hash_and_token_count(db_conn, loaded):
    replay = Replay()
    summary = embed_chunks(db_conn, _client(replay), MODEL)

    rows = {row[0]: row for row in _rows(db_conn)}
    stored_ids = set(rows) & {c.chunk_id for c in loaded}
    assert stored_ids == {c.chunk_id for c in loaded}
    assert summary.embedded == len(rows)
    for chunk in loaded:
        _, model, dims, sha, tokens, live_dims = rows[chunk.chunk_id]
        expected_sha = hashlib.sha256(resolve(db_conn, chunk.chunk_id).encode()).hexdigest()
        assert (model, dims, live_dims) == (MODEL, 1024, 1024)
        assert sha == expected_sha
        assert tokens == RECORDED_TOKENS
    assert any(c.section == "preamble" for c in loaded)


def test_rerun_skips_chunks_whose_vector_is_current(db_conn, loaded):
    first = embed_chunks(db_conn, _client(Replay()), MODEL)
    replay = Replay()
    second = embed_chunks(db_conn, _client(replay), MODEL)
    assert (second.embedded, second.skipped, second.api_tokens) == (0, first.embedded, 0)
    assert replay.requests == []


def test_rerun_re_embeds_a_chunk_whose_text_hash_changed(db_conn, loaded):
    embed_chunks(db_conn, _client(Replay()), MODEL)
    stale = loaded[0].chunk_id
    db_conn.execute(
        "UPDATE chunk_embeddings SET text_sha256 = %s WHERE chunk_id = %s", ("0" * 64, stale)
    )
    db_conn.commit()
    replay = Replay()
    summary = embed_chunks(db_conn, _client(replay), MODEL)
    assert summary.embedded == 1
    assert json.loads(replay.requests[0].content)["input"] == [resolve(db_conn, stale)]


def test_a_chunk_over_the_input_limit_fails_before_any_call(db_conn, loaded):
    longest = max(loaded, key=lambda c: c.token_count)
    replay = Replay()
    with pytest.raises(ChunkTooLong, match=longest.chunk_id):
        embed_chunks(db_conn, _client(replay), MODEL, max_input_tokens=longest.token_count - 1)
    assert replay.requests == []
    assert _rows(db_conn) == []



# ── Voyage client ─────────────────────────────────────────────────────────────

def test_request_embeds_one_document_with_truncation_off():
    replay = Replay()
    embedding = _client(replay).embed_document("Revenue was $1 billion.", MODEL)
    (request,) = replay.requests
    assert str(request.url) == VOYAGE_URL
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert json.loads(request.content) == {
        "input": ["Revenue was $1 billion."], "model": MODEL,
        "input_type": "document", "truncation": False,
    }
    assert len(embedding.vector) == 1024 and embedding.api_token_count == RECORDED_TOKENS


def test_rate_limit_is_retried_with_backoff():
    recorded = json.loads(RECORDED.read_text())
    statuses = iter([429, 503, 200])
    waits: list[float] = []

    def handler(request):
        status = next(statuses)
        return httpx.Response(status, json=recorded if status == 200 else {"detail": "slow"})

    client = VoyageClient(
        api_key=FAKE_KEY, http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=waits.append,
    )
    assert client.embed_document("x", MODEL).api_token_count == RECORDED_TOKENS
    assert waits == [2.0, 4.0]


def test_retry_after_from_the_server_sets_the_wait():
    recorded = json.loads(RECORDED.read_text())
    responses = iter([
        httpx.Response(429, headers={"Retry-After": "7"}, json={"detail": "slow"}),
        httpx.Response(429, headers={"Retry-After": "soon"}, json={"detail": "slow"}),
        httpx.Response(200, json=recorded),
    ])
    waits: list[float] = []
    client = VoyageClient(
        api_key=FAKE_KEY,
        http=httpx.Client(transport=httpx.MockTransport(lambda _r: next(responses))),
        sleep=waits.append,
    )
    client.embed_document("x", MODEL)
    assert waits == [7.0, 4.0]  # server's 7 s, then the doubled default on an unreadable header


def test_client_closes_its_connection_pool_on_exit():
    http = httpx.Client(transport=httpx.MockTransport(Replay()))
    with VoyageClient(api_key=FAKE_KEY, http=http):
        pass
    assert http.is_closed


@pytest.mark.parametrize("status", [400, 401])
def test_client_error_fails_at_once_without_the_key(status, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"detail": "bad request"})

    with pytest.raises(VoyageError, match=str(status)) as info:
        _client(handler).embed_document("x", MODEL)
    assert len(calls) == 1
    assert FAKE_KEY not in str(info.value) and FAKE_KEY not in caplog.text
    assert FAKE_KEY not in repr(_client(handler))


@pytest.mark.parametrize("change", [
    {"model": "voyage-3"},
    {"data": []},
    {"usage": {"total_tokens": 0}},
])
def test_a_response_that_does_not_match_the_request_is_refused(change):
    body = {**json.loads(RECORDED.read_text()), **change}
    with pytest.raises(VoyageError):
        _client(Replay(body)).embed_document("x", MODEL)


def test_a_vector_of_the_wrong_dimension_is_refused():
    body = json.loads(RECORDED.read_text())
    body["data"][0]["embedding"] = body["data"][0]["embedding"][:512]
    with pytest.raises(VoyageError, match="512 dimensions"):
        _client(Replay(body)).embed_document("x", MODEL)


def test_missing_key_is_refused(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(MissingApiKey):
        VoyageClient.from_env()


def test_recorded_response_holds_no_credentials():
    text = RECORDED.read_text()
    assert "pa-" not in text and "Bearer" not in text and "authorization" not in text.lower()
    assert {"data", "model", "usage"} <= set(json.loads(text))


# ── Schema ────────────────────────────────────────────────────────────────────

def test_schema_check_covers_chunk_embeddings(db_conn):
    db_conn.execute("ALTER TABLE chunk_embeddings ADD COLUMN stray integer")
    db_conn.commit()
    try:
        with pytest.raises(SchemaMismatch, match="chunk_embeddings.*stray"):
            apply_schema(db_conn)
    finally:
        db_conn.rollback()
        db_conn.execute("ALTER TABLE chunk_embeddings DROP COLUMN stray")
        db_conn.commit()


def test_reloading_a_filing_drops_its_embeddings(db_conn, fixture_records, loaded):
    embed_chunks(db_conn, _client(Replay()), MODEL)
    record = fixture_records[0]
    load_filing(db_conn, record, chunk_filing(record.filing))
    count = db_conn.execute(
        "SELECT count(*) FROM chunk_embeddings e JOIN chunks c USING (chunk_id)"
        " WHERE c.accession_no = %s", (record.meta.accession_no,),
    ).fetchone()[0]
    assert count == 0



# ── Command line ──────────────────────────────────────────────────────────────

def test_command_stops_before_the_database_without_an_api_key(monkeypatch, capsys):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@localhost:1/none")
    assert main([]) == EXIT_USAGE
    assert "VOYAGE_API_KEY" in capsys.readouterr().err


def test_command_needs_a_database_url(monkeypatch, capsys):
    monkeypatch.setenv("VOYAGE_API_KEY", FAKE_KEY)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main([]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "DATABASE_URL" in err and FAKE_KEY not in err
