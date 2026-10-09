"""The cost estimate printed before an extraction run makes any call (Step 8).

Input is counted with cl100k_base and scaled by 1.3, because Claude's tokenizer
counts about 30% more tokens for the same text (pricing page, quoted in
docs/research/claude-extraction-structured-output.md). Output cannot be counted
before the call; each call is assumed to return 2,500 tokens, the research
note's figure for triples plus thinking. The system prompt is the one cache
breakpoint: the first call on each worker is priced as a cache write, since
calls that start together cannot read what the others are still writing, and
every later call as a cache read.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from extract.request import ExtractRequest
from ingest.chunker import count_tokens
from retrieve.answer_model import TokenUsage
from retrieve.pricing import TABLE_2026_10_09, PriceTable, anthropic_cost

CLAUDE_TOKEN_FACTOR = 1.3
ASSUMED_OUTPUT_TOKENS = 2_500
EXTRACTION_PRICES = TABLE_2026_10_09


@dataclass(frozen=True, slots=True)
class CostEstimate:
    calls: int
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    usd: float
    price_table_date: str


def _claude_tokens(text: str) -> int:
    return math.ceil(count_tokens(text) * CLAUDE_TOKEN_FACTOR)


def _call_usage(request: ExtractRequest, writes_cache: bool) -> TokenUsage:
    system = _claude_tokens(request.system)
    return TokenUsage(
        input_tokens=_claude_tokens(request.user),
        output_tokens=ASSUMED_OUTPUT_TOKENS,
        cache_creation_input_tokens=system if writes_cache else 0,
        cache_read_input_tokens=0 if writes_cache else system,
    )


def estimate_cost(requests: Sequence[ExtractRequest], workers: int,
                  table: PriceTable = EXTRACTION_PRICES) -> CostEstimate:
    """The estimated cost of sending *requests*, *workers* at a time, at *table*'s prices."""
    calls = [(r.model, _call_usage(r, writes_cache=i < workers)) for i, r in enumerate(requests)]
    usages = [usage for _, usage in calls]
    return CostEstimate(
        calls=len(calls),
        input_tokens=sum(u.input_tokens for u in usages),
        cache_write_tokens=sum(u.cache_creation_input_tokens for u in usages),
        cache_read_tokens=sum(u.cache_read_input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        usd=sum(anthropic_cost(model, usage, table) for model, usage in calls),
        price_table_date=table.date,
    )
