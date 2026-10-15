"""The cost estimate printed before `python -m graph.reports` makes any call (Step 12).

Counted as extraction counts (extract.estimate): input with cl100k_base scaled
by 1.3 for Claude's tokenizer. Each call is assumed to return 3,000 tokens,
thinking at effort `high` plus a JSON report of a few hundred words. The
system prompt carries the one cache breakpoint: the first call on each worker
is priced as a cache write, every later call as a cache read.

A top request can be built only from its lower reports. When they are not all
in the response cache, a top call is sized as its template plus
ASSUMED_LOWER_REPORT_TOKENS per lower report it reads.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from string import Template

from extract.estimate import CLAUDE_TOKEN_FACTOR
from graph.report_prompt import TOP_FIELDS, ReportPrompt
from graph.report_request import REPORT_MODEL, REPORT_PRICES, ReportRequest
from ingest.chunker import count_tokens
from resolve.estimate import CostEstimate
from retrieve.answer_model import TokenUsage
from retrieve.pricing import PriceTable, anthropic_cost

ASSUMED_OUTPUT_TOKENS = 3_000
ASSUMED_LOWER_REPORT_TOKENS = 500


@dataclass(frozen=True, slots=True)
class _Call:
    system: int
    user: int


def _claude_tokens(text: str) -> int:
    return math.ceil(count_tokens(text) * CLAUDE_TOKEN_FACTOR)


def estimate_reports(requests: Sequence[ReportRequest], assumed_tops: Sequence[int],
                     prompt: ReportPrompt, workers: int,
                     table: PriceTable = REPORT_PRICES) -> CostEstimate:
    """The estimated cost of *requests* plus one top call per entry of *assumed_tops*,
    each the number of lower reports that call would read."""
    system = _claude_tokens(prompt.system)
    template = _claude_tokens(Template(prompt.top_template).substitute(
        dict.fromkeys(TOP_FIELDS, "")))
    calls = [*(_Call(system, _claude_tokens(r.user)) for r in requests),
             *(_Call(system, template + n * ASSUMED_LOWER_REPORT_TOKENS) for n in assumed_tops)]
    usd = 0.0
    for i, call in enumerate(calls):
        writes = i < workers
        usage = TokenUsage(input_tokens=call.user, output_tokens=ASSUMED_OUTPUT_TOKENS,
                           cache_creation_input_tokens=call.system if writes else 0,
                           cache_read_input_tokens=0 if writes else call.system)
        usd += anthropic_cost(REPORT_MODEL, usage, table)
    return CostEstimate(calls=len(calls), input_tokens=sum(c.system + c.user for c in calls),
                        output_tokens=ASSUMED_OUTPUT_TOKENS * len(calls), usd=usd,
                        price_table_date=table.date)
