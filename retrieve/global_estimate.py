"""A pre-run cost estimate for graph_global's map and reduce calls (Step 12).

Meant to run high, like ``retrieve.cypher_estimate``: text is counted with
cl100k_base times ``TOKEN_MARGIN``, every call's system prompt is priced as a
cache write, and output, thinking included, is assumed to be
``ASSUMED_OUTPUT_TOKENS`` per call.  A reduce call cannot be built before its
map calls return, so each one is assumed to read a full reduce budget of points.
"""
from __future__ import annotations

from collections.abc import Iterable

from ingest.chunker import count_tokens
from retrieve.global_map import MAP_MODEL, MapRequest
from retrieve.global_prompts import GlobalPrompt
from retrieve.global_reduce import REDUCE_BUDGET_TOKENS, REDUCE_MODEL
from retrieve.pricing import PRICES

TOKEN_MARGIN = 2.0
ASSUMED_OUTPUT_TOKENS = 1_500
_TOKENS_PER_MTOK = 1_000_000


def estimate_usd(map_requests: Iterable[MapRequest], reduce_prompt: GlobalPrompt,
                 reduce_calls: int, reduce_budget: int = REDUCE_BUDGET_TOKENS) -> float:
    """The estimated cost of *map_requests* and *reduce_calls* reduce calls."""
    def call(model: str, system: str, user_tokens: float) -> float:
        price = PRICES[model]
        return (count_tokens(system) * TOKEN_MARGIN * price.cache_write_per_mtok
                + user_tokens * price.input_per_mtok
                + ASSUMED_OUTPUT_TOKENS * price.output_per_mtok) / _TOKENS_PER_MTOK

    maps = sum(call(MAP_MODEL, r.system, count_tokens(r.user) * TOKEN_MARGIN)
               for r in map_requests)
    reduce_user = (count_tokens(reduce_prompt.user_template) + reduce_budget) * TOKEN_MARGIN
    return maps + reduce_calls * call(REDUCE_MODEL, reduce_prompt.system, reduce_user)
