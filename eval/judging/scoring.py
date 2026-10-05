"""Judged scores for one answer in one judge run (Step 5, ticket 07).

The four answerable classes get Ragas faithfulness, Ragas answer relevancy and
citation support.  Decline records get decline correctness and unanswerable
records get not-found correctness, so those behaviours sit in their own rows.

- Faithfulness judges the kept sentences, without citation markers, against
  every retrieved chunk.  It is skipped (None) when no sentence was kept, and
  None when Ragas finds no statements in them.
- Answer relevancy judges what a reader sees (the fixed decline or not-found
  sentence included), without citation markers.  Skipped for an empty answer.
- Citation support asks whether each kept sentence's cited chunks support it;
  the score is supported sentences / kept sentences.  Skipped with none kept.
- Decline and not-found correctness are 1 or 0 from the behaviour judge.

A reply the judge cannot use is recorded against its metric, and the other
metrics still run.  Every judged number is uncalibrated until judge calibration.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from importlib.metadata import version
from types import MappingProxyType
from typing import Protocol

from ragas.metrics.collections import AnswerRelevancy, Faithfulness

from eval.judging import prompts
from eval.judging.claude import (
    JUDGE_EFFORT,
    JUDGE_MAX_TOKENS,
    JUDGE_MODEL,
    JUDGE_RUNS,
    ClaudeJudge,
    JudgeReplyError,
)
from eval.judging.embedder import VoyageRagasEmbedding
from eval.schema import Class
from ingest.voyage import DEFAULT_MODEL, QueryEmbedder
from retrieve.answer import Answer
from retrieve.answer_model import AnswerModel, Effort
from retrieve.answer_prompt import SourceChunk
from retrieve.citations import strip_citations
from retrieve.pricing import embedding_cost

UNCALIBRATED = "uncalibrated"
RELEVANCY_STRICTNESS = 3  # Ragas's default: questions generated per answer
ANSWER_METRICS = ("faithfulness", "answer_relevancy", "citation_support")
_BEHAVIOUR_METRICS = MappingProxyType({"decline": ("decline_correct", "decline"),
                                       "unanswerable": ("not_found_correct", "not_found")})


def metrics_for(class_: Class) -> tuple[str, ...]:
    """The judged metrics a record of *class_* gets."""
    if class_ in _BEHAVIOUR_METRICS:
        return (_BEHAVIOUR_METRICS[class_][0],)
    return ANSWER_METRICS


@dataclass(frozen=True)
class JudgeInput:
    question: str
    class_: Class
    answer: Answer
    sources: tuple[SourceChunk, ...]  # every chunk the answer model saw


@dataclass(frozen=True)
class RunScores:
    """One judge run's scores for one question.  None: skipped, undefined or failed."""

    run: int
    scores: Mapping[str, float | None]
    errors: Mapping[str, str]  # metric -> why the judge's reply could not be scored
    cost_usd: float
    calls: int
    replayed: int  # calls answered from the response cache


@dataclass(frozen=True)
class JudgeConfig:
    """What the results header records about the judges."""

    model: str = JUDGE_MODEL
    effort: Effort = JUDGE_EFFORT
    max_tokens: int = JUDGE_MAX_TOKENS
    runs: int = JUDGE_RUNS
    label: str = UNCALIBRATED
    ragas_version: str = field(default_factory=lambda: version("ragas"))
    relevancy_embedding: str = DEFAULT_MODEL
    relevancy_strictness: int = RELEVANCY_STRICTNESS
    prompts: Mapping[str, Mapping[str, str]] = field(default_factory=prompts.prompt_hashes)

    def as_header(self) -> dict[str, object]:
        return {**asdict(self), "prompts": {k: dict(v) for k, v in self.prompts.items()}}


class Judge(Protocol):
    config: JudgeConfig

    def judge(self, item: JudgeInput, run: int) -> RunScores: ...


class ClaudeJudges:
    """The real judges: ``claude-opus-5-5`` through the response cache, Voyage for relevancy."""

    def __init__(self, backend: AnswerModel, embedder: QueryEmbedder,
                 config: JudgeConfig | None = None):
        self._backend = backend
        self._embedder = embedder
        self.config = config if config is not None else JudgeConfig()

    def judge(self, item: JudgeInput, run: int) -> RunScores:
        llm = ClaudeJudge(self._backend, run, self.config.model, self.config.effort,
                          self.config.max_tokens)
        embeddings = VoyageRagasEmbedding(self._embedder, self.config.relevancy_embedding)
        scorers = _scorers(item, llm, embeddings, self.config.relevancy_strictness)
        scores: dict[str, float | None] = {}
        errors: dict[str, str] = {}
        for name in metrics_for(item.class_):
            try:
                scores[name] = scorers[name]()
            except JudgeReplyError as exc:
                scores[name], errors[name] = None, str(exc)
        return RunScores(
            run=run,
            scores=MappingProxyType(scores),
            errors=MappingProxyType(errors),
            cost_usd=llm.cost_usd + embedding_cost(embeddings.model, embeddings.tokens),
            calls=len(llm.calls),
            replayed=llm.replayed,
        )


def _defined(value: float) -> float | None:
    return None if math.isnan(value) else float(value)


def _scorers(item: JudgeInput, llm: ClaudeJudge, embeddings: VoyageRagasEmbedding,
             strictness: int) -> dict[str, Callable[[], float | None]]:
    check = item.answer.citation_check

    def faithfulness() -> float | None:
        kept = strip_citations(check.text)
        if not kept:
            return None
        result = asyncio.run(Faithfulness(llm=llm).ascore(
            user_input=item.question, response=kept,
            retrieved_contexts=[s.text for s in item.sources]))
        return _defined(result.value)

    def answer_relevancy() -> float | None:
        shown = strip_citations(item.answer.text)
        if not shown:
            return None
        metric = AnswerRelevancy(llm=llm, embeddings=embeddings, strictness=strictness)
        return _defined(asyncio.run(metric.ascore(user_input=item.question, response=shown)).value)

    def citation_support() -> float | None:
        if not check.kept:
            return None
        reply = llm.generate(
            prompts.citation_support_prompt(item.question, check.kept, item.sources),
            prompts.CitationSupportOutput)
        numbers = [v.sentence for v in reply.verdicts]
        if numbers != list(range(1, len(check.kept) + 1)):
            raise JudgeReplyError(
                f"citation support verdicts numbered {numbers}, expected one per sentence "
                f"1 to {len(check.kept)}")
        return sum(v.supported for v in reply.verdicts) / len(reply.verdicts)

    def behaviour(kind: prompts.Behaviour) -> Callable[[], float]:
        def score() -> float:
            reply = llm.generate(prompts.behaviour_prompt(item.question, item.answer.text, kind),
                                 prompts.BehaviourVerdict)
            return 1.0 if reply.correct else 0.0
        return score

    return {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "citation_support": citation_support,
        **{metric: behaviour(kind) for metric, kind in _BEHAVIOUR_METRICS.values()},
    }
