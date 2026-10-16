"""graph_global's reduce step: which map points it reads, and its call (Step 12, ADR-0008).

The reduce call reads the kept map points that scored above 0, by score, then
community rank, then their order in the reply, added while their cl100k_base
count stays within the budget (the first is always added).  It is a plain
answer call, ``AnswerRequest`` on the answer model at effort ``high``, whose
reply carries ``answer/v1``'s status line, so ``retrieve.answer`` reads it
like any answer.
"""
from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass, replace
from string import Template

from ingest.chunker import count_tokens
from retrieve.answer_model import ANSWER_EFFORT, ANSWER_MAX_TOKENS, ANSWER_MODEL, AnswerRequest
from retrieve.arm import MapCall, MapPoint
from retrieve.global_prompts import GlobalPrompt

REDUCE_BUDGET_TOKENS = 8_000
REDUCE_MODEL = ANSWER_MODEL
REDUCE_EFFORT = ANSWER_EFFORT
REDUCE_MAX_TOKENS = ANSWER_MAX_TOKENS


@dataclass(frozen=True)
class ReduceInput:
    points: tuple[MapPoint, ...]  # in the order the reduce call reads them
    over_zero: int  # kept points that scored above 0
    tokens: int  # the selected points' cl100k_base count
    positions: frozenset[tuple[int, int]]  # (call index, point index) of each point

    @property
    def cut_by_budget(self) -> int:
        return self.over_zero - len(self.points)


def point_line(point: MapPoint) -> str:
    """One point as the reduce call reads it."""
    return f'<point score="{point.score}">{html.escape(point.text, quote=False)}</point>'


def select_points(calls: Sequence[MapCall], budget: int = REDUCE_BUDGET_TOKENS) -> ReduceInput:
    """The kept points above 0, best first, within *budget* tokens."""
    ranked = sorted(
        ((p, call.rank, c, i) for c, call in enumerate(calls) for i, p in enumerate(call.points)
         if p.kept and p.score),
        key=lambda item: (-(item[0].score or 0), item[1], item[3]))
    chosen: list[tuple[MapPoint, int, int]] = []
    tokens = 0
    for point, _, c, i in ranked:
        size = count_tokens(point_line(point))
        if chosen and tokens + size > budget:
            break
        chosen.append((point, c, i))
        tokens += size
    return ReduceInput(tuple(p for p, _, _ in chosen), len(ranked), tokens,
                       frozenset((c, i) for _, c, i in chosen))


def mark_reduced(calls: Sequence[MapCall], selected: ReduceInput) -> tuple[MapCall, ...]:
    """*calls* with each point the reduce call reads marked ``in_reduce``."""
    return tuple(replace(call, points=tuple(replace(p, in_reduce=True)
                                            if (c, i) in selected.positions else p
                                            for i, p in enumerate(call.points)))
                 for c, call in enumerate(calls))


def build_reduce_request(prompt: GlobalPrompt, question: str,
                         points: Sequence[MapPoint]) -> AnswerRequest:
    user = Template(prompt.user_template).substitute(
        points="\n".join(point_line(p) for p in points), question=question)
    return AnswerRequest(model=REDUCE_MODEL, effort=REDUCE_EFFORT, max_tokens=REDUCE_MAX_TOKENS,
                         system=prompt.system, user=user)
