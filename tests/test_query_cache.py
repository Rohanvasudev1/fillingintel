"""Query embeddings and their disk cache (Step 5, ticket 03).

The real ``VoyageClient`` runs over an ``httpx.MockTransport`` that replays the
recorded voyage-4-large response.  A query response has the same shape as a
document response, so the one recording serves both.
"""
import json
from pathlib import Path

import httpx
import pytest

from ingest.voyage import VOYAGE_URL, VoyageClient
from retrieve.query_cache import CachedQueryEmbedder, CacheError

RECORDED = Path(__file__).parent / "fixtures" / "voyage" / "document_response.json"
MODEL = "voyage-4-large"
FAKE_KEY = "pa-test-key-not-real-0123456789"
QUESTION = "What was NVIDIA's total revenue for fiscal year 2026?"


class Replay:
    def __init__(self):
        self.body = json.loads(RECORDED.read_text())
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.body)


def _client(replay: Replay) -> VoyageClient:
    return VoyageClient(
        api_key=FAKE_KEY,
        http=httpx.Client(transport=httpx.MockTransport(replay)),
        sleep=lambda _seconds: None,
    )


def test_a_query_is_embedded_with_the_query_input_type_and_truncation_off():
    replay = Replay()
    embedding = _client(replay).embed_query(QUESTION, MODEL)
    (request,) = replay.requests
    assert str(request.url) == VOYAGE_URL
    assert json.loads(request.content) == {
        "input": [QUESTION], "model": MODEL, "input_type": "query", "truncation": False,
    }
    assert len(embedding.vector) == 1024


def test_a_repeated_question_is_read_from_disk_without_a_call(tmp_path):
    replay = Replay()
    first = CachedQueryEmbedder(_client(replay), tmp_path).embed_query(QUESTION, MODEL)
    fresh_replay = Replay()
    second = CachedQueryEmbedder(_client(fresh_replay), tmp_path).embed_query(QUESTION, MODEL)
    assert len(replay.requests) == 1
    assert fresh_replay.requests == []
    assert second.vector == first.vector
    assert second.api_token_count == first.api_token_count
    assert (first.from_cache, second.from_cache) == (False, True)


def test_the_cache_is_keyed_by_model_and_text_hash(tmp_path):
    replay = Replay()
    cache = CachedQueryEmbedder(_client(replay), tmp_path)
    cache.embed_query(QUESTION, MODEL)
    cache.embed_query(QUESTION + " ", MODEL)  # different text, different key
    assert len(replay.requests) == 2
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.json"))
    assert len(files) == 2
    assert all(f.startswith(f"{MODEL}/") for f in files)
    stored = json.loads((tmp_path / files[0]).read_text())
    assert set(stored) == {"model", "text_sha256", "vector", "api_token_count"}
    assert QUESTION not in json.dumps(stored)  # keyed by hash; the question text is not stored


def test_an_unreadable_cache_file_stops_with_its_path(tmp_path):
    replay = Replay()
    cache = CachedQueryEmbedder(_client(replay), tmp_path)
    cache.embed_query(QUESTION, MODEL)
    (path,) = tmp_path.rglob("*.json")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CacheError, match=path.name):
        CachedQueryEmbedder(_client(Replay()), tmp_path).embed_query(QUESTION, MODEL)


def test_a_cache_file_with_the_wrong_dimensions_is_refused(tmp_path):
    cache = CachedQueryEmbedder(_client(Replay()), tmp_path)
    cache.embed_query(QUESTION, MODEL)
    (path,) = tmp_path.rglob("*.json")
    stored = json.loads(path.read_text())
    path.write_text(json.dumps({**stored, "vector": stored["vector"][:10]}), encoding="utf-8")
    with pytest.raises(CacheError, match="dimensions"):
        CachedQueryEmbedder(_client(Replay()), tmp_path).embed_query(QUESTION, MODEL)
