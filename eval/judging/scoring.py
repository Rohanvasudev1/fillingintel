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

Citation support and the behaviour metrics also keep their yes/no verdicts (one
per kept sentence, or the one behaviour verdict), so two judges can be compared
verdict by verdict (``eval.judging.spotcheck``).

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
from eval.judging.embedder import VoyageRagasEmbedding
from eval.judging.openai_backend import NO_USAGE, OpenAIUsage, total_usage
from eval.judging.openai_judge import (
    JUDGE_EFFORT,
    JUDGE_MAX_OUTPUT_TOKENS,
    JUDGE_MODEL,
    JUDGE_PROVIDER,
    JUDGE_RUNS,
    OPENAI_SDK_VERSION,
    JudgeReplyError,
    OpenAIJudge,
)
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
    usage: OpenAIUsage = NO_USAGE  # judge tokens over this run's calls, cached ones included
    # metric -> the yes/no verdicts behind its score, for the verdict metrics that produced one
    verdicts: Mapping[str, tuple[bool, ...]] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class JudgeConfig:
    """What the results header records about the judges."""

    provider: str = JUDGE_PROVIDER
    model: str = JUDGE_MODEL
    effort: Effort = JUDGE_EFFORT
    max_output_tokens: int = JUDGE_MAX_OUTPUT_TOKENS
    runs: int = JUDGE_RUNS
    openai_version: str = OPENAI_SDK_VERSION
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


class RagasJudges:
    """The real judges: the configured OpenAI model through the response cache, Voyage for
    relevancy."""

    def __init__(self, backend: AnswerModel, embedder: QueryEmbedder,
                 config: JudgeConfig | None = None):
        self._backend = backend
        self._embedder = embedder
        self.config = config if config is not None else JudgeConfig()

    def judge(self, item: JudgeInput, run: int) -> RunScores:
        llm = OpenAIJudge(self._backend, run, self.config.model, self.config.effort,
                          self.config.max_output_tokens)
        embeddings = VoyageRagasEmbedding(self._embedder, self.config.relevancy_embedding)
        scorers = _scorers(item, llm, embeddings, self.config.relevancy_strictness)
        scores: dict[str, float | None] = {}
        errors: dict[str, str] = {}
        verdicts: dict[str, tuple[bool, ...]] = {}
        for name in metrics_for(item.class_):
            try:
                scored = scorers[name]()
            except JudgeReplyError as exc:
                scores[name], errors[name] = None, str(exc)
                continue
            scores[name] = scored.value
            if scored.verdicts:
                verdicts[name] = scored.verdicts
        return RunScores(
            run=run,
            scores=MappingProxyType(scores),
            errors=MappingProxyType(errors),
            cost_usd=llm.cost_usd + embedding_cost(embeddings.model, embeddings.tokens),
            calls=len(llm.calls),
            replayed=llm.replayed,
            usage=total_usage(c.usage for c in llm.calls),
            verdicts=MappingProxyType(verdicts),
        )


@dataclass(frozen=True)
class _Scored:
    value: float | None
    verdicts: tuple[bool, ...] = ()  # empty for the score metrics


def _defined(value: float) -> float | None:
    return None if math.isnan(value) else float(value)


def _scorers(item: JudgeInput, llm: OpenAIJudge, embeddings: VoyageRagasEmbedding,
             strictness: int) -> dict[str, Callable[[], _Scored]]:
    check = item.answer.citation_check

    def faithfulness() -> _Scored:
        kept = strip_citations(check.text)
        if not kept:
            return _Scored(None)
        result = asyncio.run(Faithfulness(llm=llm).ascore(
            user_input=item.question, response=kept,
            retrieved_contexts=[s.text for s in item.sources]))
        return _Scored(_defined(result.value))

    def answer_relevancy() -> _Scored:
        shown = strip_citations(item.answer.text)
        if not shown:
            return _Scored(None)
        metric = AnswerRelevancy(llm=llm, embeddings=embeddings, strictness=strictness)
        return _Scored(_defined(
            asyncio.run(metric.ascore(user_input=item.question, response=shown)).value))

    def citation_support() -> _Scored:
        if not check.kept:
            return _Scored(None)
        reply = llm.generate(
            prompts.citation_support_prompt(item.question, check.kept, item.sources),
            prompts.CitationSupportOutput)
        numbers = [v.sentence for v in reply.verdicts]
        if numbers != list(range(1, len(check.kept) + 1)):
            raise JudgeReplyError(
                f"citation support verdicts numbered {numbers}, expected one per sentence "
                f"1 to {len(check.kept)}")
        supported = tuple(v.supported for v in reply.verdicts)
        return _Scored(sum(supported) / len(supported), supported)

    def behaviour(kind: prompts.Behaviour) -> Callable[[], _Scored]:
        def score() -> _Scored:
            reply = llm.generate(prompts.behaviour_prompt(item.question, item.answer.text, kind),
                                 prompts.BehaviourVerdict)
            return _Scored(1.0 if reply.correct else 0.0, (reply.correct,))
        return score

    return {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "citation_support": citation_support,
        **{metric: behaviour(kind) for metric, kind in _BEHAVIOUR_METRICS.values()},
    }
