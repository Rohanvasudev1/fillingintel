"""The batched Voyage path and its disk cache, for entity names (Step 9).

The real ``VoyageClient`` runs over an ``httpx.MockTransport``. Each response
takes the recorded voyage-4-large response's shape, with one vector per input
made from the recorded vector, so the order of the inputs can be checked.
"""
import json
from pathlib import Path

import httpx
import pytest

from ingest.voyage import VOYAGE_URL, VoyageClient, VoyageError
from retrieve.query_cache import CachedBatchEmbedder, CacheError

RECORDED = Path(__file__).parent / "fixtures" / "voyage" / "document_response.json"
MODEL = "voyage-4-large"
FAKE_KEY = "pa-test-key-not-real-0123456789"


def _vector(i: int) -> list[float]:
    base = json.loads(RECORDED.read_text())["data"][0]["embedding"]
    return base[i:] + base[:i]  # the recorded vector, rotated: a distinct real-valued vector


class Replay:
    """Answers each request with one vector per input, given in reverse index order."""

    def __init__(self, drop: int = 0):
        self.requests: list[dict] = []
        self._drop = drop

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        n = len(body["input"]) - self._drop
        data = [{"object": "embedding", "embedding": _vector(i), "index": i}
                for i in reversed(range(n))]
        return httpx.Response(200, json={"object": "list", "data": data, "model": MODEL,
                                         "usage": {"total_tokens": 7 * n}})


def _client(replay: Replay) -> VoyageClient:
    return VoyageClient(api_key=FAKE_KEY, http=httpx.Client(transport=httpx.MockTransport(replay)),
                        sleep=lambda _seconds: None)


def test_a_batch_is_one_request_with_one_input_type_and_vectors_in_input_order():
    replay = Replay()
    batch = _client(replay).embed_batch(["Organization: Groq", "Regulation: GDPR"], MODEL,
                                        "document")
    (request,) = replay.requests
    assert request == {"input": ["Organization: Groq", "Regulation: GDPR"], "model": MODEL,
                       "input_type": "document", "truncation": False}
    assert [list(v) for v in batch.vectors] == [_vector(0), _vector(1)]
    assert batch.total_tokens == 14


def test_a_batch_response_missing_a_vector_is_refused():
    with pytest.raises(VoyageError, match="expected 2"):
        _client(Replay(drop=1)).embed_batch(["a", "b"], MODEL, "document")


def test_an_empty_batch_makes_no_call():
    replay = Replay()
    assert _client(replay).embed_batch([], MODEL, "document").vectors == ()
    assert replay.requests == []


def test_a_cached_rerun_makes_zero_calls(tmp_path):
    texts = ["Organization: Groq", "Organization: Groq, Inc.", "Regulation: GDPR"]
    replay = Replay()
    first = CachedBatchEmbedder(_client(replay), tmp_path).embed(texts, MODEL, "document")
    rerun_replay = Replay()
    rerun = CachedBatchEmbedder(_client(rerun_replay), tmp_path)
    assert rerun.embed(texts, MODEL, "document").vectors == first.vectors
    assert rerun_replay.requests == []
    assert rerun.calls == 0 and rerun.tokens == 0


def test_only_uncached_texts_are_sent_each_once(tmp_path):
    replay = Replay()
    cache = CachedBatchEmbedder(_client(replay), tmp_path)
    cache.embed(["Organization: Groq"], MODEL, "document")
    result = cache.embed(["Regulation: GDPR", "Organization: Groq", "Regulation: GDPR"], MODEL,
                         "document")
    assert [r["input"] for r in replay.requests] == [["Organization: Groq"], ["Regulation: GDPR"]]
    assert result.vectors[0] == result.vectors[2]
    assert (cache.calls, cache.tokens) == (2, 14)


def test_the_cache_is_keyed_by_input_type(tmp_path):
    replay = Replay()
    cache = CachedBatchEmbedder(_client(replay), tmp_path)
    cache.embed(["Organization: Groq"], MODEL, "document")
    cache.embed(["Organization: Groq"], MODEL, "query")
    assert [r["input_type"] for r in replay.requests] == ["document", "query"]


def test_uncached_texts_are_sent_in_batches_of_the_batch_size(tmp_path):
    replay = Replay()
    cache = CachedBatchEmbedder(_client(replay), tmp_path, batch_size=2)
    cache.embed(["a", "b", "c"], MODEL, "document")
    assert [r["input"] for r in replay.requests] == [["a", "b"], ["c"]]


def test_a_damaged_cache_file_stops_the_run(tmp_path):
    cache = CachedBatchEmbedder(_client(Replay()), tmp_path)
    cache.embed(["Organization: Groq"], MODEL, "document")
    (path,) = (tmp_path / MODEL / "document").iterdir()
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CacheError, match="unexpected fields"):
        CachedBatchEmbedder(_client(Replay()), tmp_path).embed(["Organization: Groq"], MODEL,
                                                               "document")


def test_the_cache_stores_no_text(tmp_path):
    CachedBatchEmbedder(_client(Replay()), tmp_path).embed(["Organization: Groq"], MODEL,
                                                           "document")
    (path,) = (tmp_path / MODEL / "document").iterdir()
    assert "Groq" not in path.read_text(encoding="utf-8")
    assert VOYAGE_URL not in path.read_text(encoding="utf-8")
