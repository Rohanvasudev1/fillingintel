"""The pre-run cost estimate for Cypher calls (Step 11 ticket 06)."""
import pytest

from ingest.chunker import count_tokens
from retrieve.cypher_estimate import ASSUMED_OUTPUT_TOKENS, TOKEN_MARGIN, estimate_usd
from retrieve.cypher_request import CypherRequest
from retrieve.pricing import PRICES

SONNET = PRICES["claude-sonnet-5-5"]
SYSTEM, USER = "system text", "user text"


def _request() -> CypherRequest:
    return CypherRequest(system=SYSTEM, messages=(("user", USER),))


def _cost(system_price: float) -> float:
    return (count_tokens(SYSTEM) * TOKEN_MARGIN * system_price
            + count_tokens(USER) * TOKEN_MARGIN * SONNET.input_per_mtok
            + ASSUMED_OUTPUT_TOKENS * SONNET.output_per_mtok) / 1_000_000


def test_the_first_call_writes_the_system_prompt_to_the_cache_and_later_calls_read_it():
    assert estimate_usd([_request()]) == pytest.approx(_cost(SONNET.cache_write_per_mtok))
    assert estimate_usd([_request(), _request()]) == pytest.approx(
        _cost(SONNET.cache_write_per_mtok) + _cost(SONNET.cache_read_per_mtok))


def test_an_expected_repair_share_scales_the_estimate():
    assert estimate_usd([_request()], repair_share=0.5) == pytest.approx(
        1.5 * estimate_usd([_request()]))


def test_no_calls_cost_nothing():
    assert estimate_usd([]) == 0.0
