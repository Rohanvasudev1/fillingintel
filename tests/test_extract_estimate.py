"""The pre-run cost estimate for extraction (Step 8, ticket 03)."""
import math

import pytest

from extract.estimate import ASSUMED_OUTPUT_TOKENS, CLAUDE_TOKEN_FACTOR, estimate_cost
from extract.prompt import load_prompt
from extract.request import build_request
from ingest.chunker import count_tokens
from tests.extract_fakes import NVDA_FILING, nvda_chunks  # noqa: F401 (fixture)

IN, WRITE, READ, OUT = 2.00, 2.50, 0.10, 10.00  # Sonnet 5.5, 2026-10-09 table


@pytest.fixture(scope="module")
def requests(nvda_chunks):  # noqa: F811
    prompt = load_prompt()
    return [build_request(prompt, NVDA_FILING, c) for c in list(nvda_chunks.values())[:6]]


def _claude(text: str) -> int:
    return math.ceil(count_tokens(text) * CLAUDE_TOKEN_FACTOR)


def test_no_requests_cost_nothing():
    estimate = estimate_cost([], workers=4)
    assert (estimate.calls, estimate.usd) == (0, 0.0)


def test_one_request_writes_the_system_prompt_to_the_cache(requests):
    request = requests[0]
    estimate = estimate_cost([request], workers=4)
    system, user = _claude(request.system), _claude(request.user)
    assert estimate.calls == 1
    assert estimate.cache_write_tokens == system
    assert estimate.cache_read_tokens == 0
    assert estimate.input_tokens == user
    assert estimate.output_tokens == ASSUMED_OUTPUT_TOKENS
    assert estimate.usd == pytest.approx(
        (system * WRITE + user * IN + ASSUMED_OUTPUT_TOKENS * OUT) / 1_000_000)
    assert estimate.price_table_date == "2026-10-09"


def test_the_first_call_on_each_worker_writes_and_later_calls_read(requests):
    estimate = estimate_cost(requests, workers=4)
    system = _claude(requests[0].system)
    assert estimate.calls == 6
    assert estimate.cache_write_tokens == 4 * system
    assert estimate.cache_read_tokens == 2 * system


def test_input_allows_for_claude_counting_about_thirty_percent_more_than_cl100k(requests):
    estimate = estimate_cost(requests[:1], workers=1)
    cl100k = count_tokens(requests[0].system) + count_tokens(requests[0].user)
    assert CLAUDE_TOKEN_FACTOR == pytest.approx(1.3)
    assert estimate.input_tokens + estimate.cache_write_tokens >= 1.3 * cl100k
