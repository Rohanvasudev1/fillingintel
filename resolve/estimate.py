"""The cost estimate printed before adjudication makes any call (Step 9).

Counted as extraction counts (extract.estimate): input with cl100k_base scaled
by 1.3 for Claude's tokenizer. Each call is assumed to return 1,200 tokens, an
allowance for thinking at effort `high` plus a short JSON reply: ten recorded
claude-haiku-5-5 replies under resolve/v2 ran 475 to 1,487, mean about 830. The system
prompt carries the one cache breakpoint: the first call on each worker is
priced as a cache write, every later call as a cache read.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from extract.estimate import CLAUDE_TOKEN_FACTOR
from ingest.chunker import count_tokens
from resolve.request import ADJUDICATION_PRICES
from retrieve.answer_model import TokenUsage
from retrieve.pricing import PriceTable, anthropic_cost

ASSUMED_OUTPUT_TOKENS = 1_200


class PricedRequest(Protocol):
    """An adjudicator request: resolve.request's, or persist.request's (Step 10)."""

    @property
    def model(self) -> str: ...

    @property
    def system(self) -> str: ...

    @property
    def user(self) -> str: ...


@dataclass(frozen=True, slots=True)
class CostEstimate:
    calls: int
    input_tokens: int
    output_tokens: int
    usd: float
    price_table_date: str


def _claude_tokens(text: str) -> int:
    return math.ceil(count_tokens(text) * CLAUDE_TOKEN_FACTOR)


def estimate_cost(requests: Sequence[PricedRequest], workers: int,
                  table: PriceTable = ADJUDICATION_PRICES) -> CostEstimate:
    """The estimated cost of sending *requests*, *workers* at a time, at *table*'s prices."""
    usd = 0.0
    input_tokens = 0
    for i, request in enumerate(requests):
        system, user = _claude_tokens(request.system), _claude_tokens(request.user)
        writes = i < workers
        usage = TokenUsage(input_tokens=user, output_tokens=ASSUMED_OUTPUT_TOKENS,
                           cache_creation_input_tokens=system if writes else 0,
                           cache_read_input_tokens=0 if writes else system)
        usd += anthropic_cost(request.model, usage, table)
        input_tokens += system + user
    return CostEstimate(calls=len(requests), input_tokens=input_tokens,
                        output_tokens=ASSUMED_OUTPUT_TOKENS * len(requests), usd=usd,
                        price_table_date=table.date)
