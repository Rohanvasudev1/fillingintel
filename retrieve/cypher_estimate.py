"""A pre-run cost estimate for Cypher generation calls (Step 11).

Meant to run high.  Text is counted with cl100k_base times ``TOKEN_MARGIN``:
Claude counted the v1 system prompt at 7,488 tokens where cl100k_base counts
4,145 (1.81 times).  The first call writes the system prompt to the prompt
cache and later calls read it; the messages are uncached input.  Output,
thinking included, is assumed to be ``ASSUMED_OUTPUT_TOKENS`` per call: the
three recorded replies used 568 to 725.  Prices come from the results header's
table, whose Sonnet 5.5 cache read ($0.20) is above the corrected $0.10.
"""
from __future__ import annotations

from collections.abc import Iterable

from ingest.chunker import count_tokens
from retrieve.cypher_request import CYPHER_MODEL, CypherRequest
from retrieve.pricing import PRICES

TOKEN_MARGIN = 2.0
ASSUMED_OUTPUT_TOKENS = 1_500
_TOKENS_PER_MTOK = 1_000_000


def estimate_usd(requests: Iterable[CypherRequest], repair_share: float = 0.0) -> float:
    """The estimated cost of *requests*, sent in order, plus *repair_share* of them
    again for repairs."""
    price = PRICES[CYPHER_MODEL]
    total = 0.0
    for i, request in enumerate(requests):
        system = count_tokens(request.system) * TOKEN_MARGIN
        messages = sum(count_tokens(text) for _, text in request.messages) * TOKEN_MARGIN
        system_price = price.cache_write_per_mtok if i == 0 else price.cache_read_per_mtok
        total += (system * system_price + messages * price.input_per_mtok
                  + ASSUMED_OUTPUT_TOKENS * price.output_per_mtok) / _TOKENS_PER_MTOK
    return total * (1 + repair_share)
