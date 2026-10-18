"""Judging the corrupted copies (Step 13 ticket 04): what ``eval.corrupt`` runs.

Each copy is judged like an ordinary answer, through the pinned judge and the
response cache, for faithfulness and citation support only.  A copy keeps its
answer's status and retrieved chunks; only its kept sentences change.  The
uncorrupted answer's faithfulness comes from its run file, and its citation
verdicts from the judge's cached replies (``judge_verdicts``).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from eval.calibration.answers import RunAnswer
from eval.calibration.catch import CopyResult
from eval.calibration.corruptors import Corruption, corrupt
from eval.calibration.judge_verdicts import cached_citation_verdicts, judge_sources
from eval.judging.runner import JudgedQuestion, judge_all
from eval.judging.scoring import Judge, JudgeInput, RunScores
from retrieve.answer import Answer, AnswerStatus
from retrieve.answer_model import TokenUsage
from retrieve.answer_prompt import SourceChunk
from retrieve.citations import CitationCheck, Sentence

METRICS = ("faithfulness", "citation_support")
# A copy is judged on 3 of the 6 calls an answer's judge run makes (2 faithfulness, 1
# citation support; relevancy's 3 are skipped), so it is priced at half its answer's
# measured judge cost, with the margin ticket 01 set for estimates from run files.
COPY_SHARE = 0.5
ESTIMATE_MARGIN = 1.5


@dataclass(frozen=True)
class Planned:
    answer: RunAnswer
    corruption: Corruption
    sources: tuple[SourceChunk, ...]


def copy_answer(answer: RunAnswer, kept: tuple[Sentence, ...]) -> Answer:
    """*answer* with *kept* as its kept sentences; no model call stands behind it."""
    cited = sum(len(s.citations) for s in kept)
    check = CitationCheck(kept=kept, dropped=(), citations=cited, citations_retrieved=cited)
    return Answer(status=cast(AnswerStatus, answer.status), text=check.text,
                  raw_text=answer.raw_text,
                  citation_check=check, model="", message_id="", stop_reason=None,
                  refusal_category=None, usage=TokenUsage(0, 0), cost_usd=0.0,
                  generation_ms=0.0, from_cache=True)


def plan(answers: Sequence[RunAnswer], texts: Mapping[str, str]
         ) -> tuple[list[Planned], Counter[str], list[dict[str, str]]]:
    """Every copy to judge, skips counted by kind, and each skip's answer and reason."""
    planned: list[Planned] = []
    skipped: Counter[str] = Counter()
    skips: list[dict[str, str]] = []
    for answer in answers:
        sources = judge_sources(answer.retrieved, texts)
        for outcome in corrupt(answer, texts):
            if isinstance(outcome, Corruption):
                planned.append(Planned(answer, outcome, sources))
            else:
                skipped[outcome.kind] += 1
                skips.append({"key": answer.key, "kind": outcome.kind, "reason": outcome.reason})
    return planned, skipped, skips


def estimate_usd(planned: Sequence[Planned]) -> float:
    return sum(p.answer.judge_cost_usd * COPY_SHARE for p in planned) * ESTIMATE_MARGIN


def fresh_usd(judged: Sequence[JudgedQuestion]) -> float:
    """The cost of every run that made at least one call the cache did not answer."""
    return sum(r.cost_usd for j in judged for r in j.runs if r.replayed < r.calls)


def _verdicts(run: RunScores) -> tuple[bool, ...] | None:
    return run.verdicts.get("citation_support") or None


def judge_planned(judge: Judge, planned: Sequence[Planned], response_cache: Path
                  ) -> tuple[list[CopyResult], list[JudgedQuestion]]:
    """Judge every copy, and read each original sentence's cached verdicts."""
    items = [JudgeInput(p.answer.question, p.answer.class_,
                        copy_answer(p.answer, p.corruption.kept), p.sources) for p in planned]
    judged = judge_all(judge, items)
    originals = {p.answer.key: cached_citation_verdicts(p.answer.question, p.answer.kept,
                                                        p.sources, response_cache)
                 for p in planned}
    results = [
        CopyResult(
            key=p.answer.key, kind=p.corruption.kind,
            sentence_index=p.corruption.sentence_index,
            original_faithfulness=p.answer.judged["faithfulness"],
            corrupted_faithfulness=tuple(r.scores.get("faithfulness") for r in j.runs),
            original_verdicts=(None if p.corruption.kind == "added_sentence"
                               else originals[p.answer.key]),
            corrupted_verdicts=tuple(_verdicts(r) for r in j.runs),
        )
        for p, j in zip(planned, judged, strict=True)
    ]
    return results, judged
