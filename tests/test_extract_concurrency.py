"""extract_filing() with several calls at a time (Step 8, ticket 03)."""
import contextvars
import threading

import pytest

from extract.pipeline import extract_filing
from extract.prompt import load_prompt
from retrieve.answer_model import ApiResponse
from tests.extract_fakes import NVDA_FILING, FakeExtractModel, body, nvda_chunks  # noqa: F401

EMPTY = {"nodes": [], "triples": []}
CALLER = contextvars.ContextVar("caller", default=None)


class _BarrierModel:
    """Every call waits until four calls are in flight at once, and records the caller's
    context variable as the call sees it."""

    def __init__(self):
        self._barrier = threading.Barrier(4, timeout=10)
        self.seen: list[object] = []
        self._lock = threading.Lock()

    def complete(self, request):
        self._barrier.wait()
        with self._lock:
            self.seen.append(CALLER.get())
        return ApiResponse(body=body(EMPTY), api_ms=1.0)


@pytest.fixture(scope="module")
def chunks(nvda_chunks):  # noqa: F811
    return list(nvda_chunks.values())[:8]


def test_four_workers_run_four_calls_at_once_in_the_callers_context(chunks):
    model = _BarrierModel()
    token = CALLER.set("the run")
    try:
        run = extract_filing(NVDA_FILING, chunks, model, load_prompt(), workers=4)
    finally:
        CALLER.reset(token)
    assert run.complete
    assert model.seen == ["the run"] * len(chunks)


def test_the_result_is_the_same_as_one_at_a_time(chunks):
    replies = {c.chunk_id: EMPTY for c in chunks}
    replies[chunks[2].chunk_id] = "not json"
    one = extract_filing(NVDA_FILING, chunks, FakeExtractModel(replies), load_prompt())
    four = extract_filing(NVDA_FILING, chunks, FakeExtractModel(replies), load_prompt(),
                          workers=4)
    assert four == one
    assert [c.chunk_id for c in four.chunks] == [c.chunk_id for c in chunks]


def test_workers_below_one_are_refused(chunks):
    with pytest.raises(ValueError, match="workers"):
        extract_filing(NVDA_FILING, chunks, FakeExtractModel({}), load_prompt(), workers=0)
