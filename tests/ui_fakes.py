"""Small builders for the UI backend's tests (Step 14)."""
from __future__ import annotations

from dataclasses import replace

from retrieve.answer import enforce_citations, not_found_without_call
from retrieve.answer_model import TokenUsage
from retrieve.arm import ArmResult, GraphRetrieval, RetrievedChunk, Routing, StageCost
from retrieve.question_filter import QuestionFilter

CHUNK = "0001045810-26-000021:0013"
NO_FILTER = QuestionFilter((), (), (), None)


def arm_result(*, embed_cost: float = 0.0, answer_cost: float = 0.0, query_cached: bool = False,
               answer_cached: bool = False, graph: GraphRetrieval | None = None,
               routing: Routing | None = None, text: str = f"A rose [{CHUNK}].",
               ) -> ArmResult:
    """An answered result citing ``CHUNK``, with the costs and cache flags a test sets."""
    check = enforce_citations(text, {CHUNK})
    answer = replace(not_found_without_call(), status="answered", text=check.text,
                     raw_text=text, citation_check=check, cost_usd=answer_cost,
                     from_cache=answer_cached, usage=TokenUsage(10, 5))
    return ArmResult(
        retrieved=(RetrievedChunk(CHUNK, 0.9),), question_filter=NO_FILTER,
        companies_without_chunks=(), embed_ms=1.0, search_ms=2.0, query_cached=query_cached,
        embed_tokens=5, embed_cost_usd=embed_cost, graph=graph, routing=routing,
        answer=answer, sources=())


def routing(*, cost: float = 0.0, from_cache: bool = False, arm: str = "vector") -> Routing:
    return Routing(predicted_class="lookup", reason="a single figure", map_name="dev_best",
                   arm=arm, effort="low", prompt_version="route/v2@00000000",
                   prompt_sha256="0" * 64,
                   cost=StageCost(ms=5.0, cost_usd=cost, from_cache=from_cache))
