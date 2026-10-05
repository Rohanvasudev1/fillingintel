"""The one interface every arm implements, so the harness runs any arm by name.

An arm receives the question text and nothing else from the eval record
(ADR-0001), so it can never see a record's labels.  Every arm reads its filter
with the shared parser in ``retrieve.question_filter``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from retrieve.answer import Answer
from retrieve.answer_prompt import AnswerPrompt, SourceChunk
from retrieve.question_filter import QuestionFilter


class ArmError(RuntimeError):
    """An arm could not start or could not answer; the message is safe to print."""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    score: float


@dataclass(frozen=True)
class ArmResult:
    """What one arm returned for one question."""

    retrieved: tuple[RetrievedChunk, ...]  # best first
    question_filter: QuestionFilter
    companies_without_chunks: tuple[str, ...]  # named companies no retrieved chunk came from
    embed_ms: float  # query embedding, from the API or the disk cache
    search_ms: float
    query_cached: bool  # cached embeddings take ~0 ms, so latency stats must tell them apart
    embed_tokens: int  # as Voyage reported them, also for a cached embedding
    embed_cost_usd: float
    answer: Answer
    sources: tuple[SourceChunk, ...]  # what the answer model saw, in retrieval order; for judges

    @property
    def retrieval_ms(self) -> float:
        return self.embed_ms + self.search_ms


@dataclass(frozen=True)
class ArmConfig:
    """What the results header records about an arm."""

    models: Mapping[str, str]  # role -> model ID, e.g. {"embedding": "voyage-4-large"}
    k: int
    chunker_version: str
    efforts: Mapping[str, str]  # role -> effort, e.g. {"answer": "high"}
    answer_prompt: AnswerPrompt
    answer_max_tokens: int


class Arm(Protocol):
    name: str
    config: ArmConfig

    def run(self, question: str) -> ArmResult: ...


@dataclass(frozen=True)
class ArmSpec:
    """A registered arm: the environment variables it needs and how to open it.

    ``open`` takes the folder of the response cache the arm's model calls go
    through and returns a context manager, so the arm owns and closes its own
    connections.  The harness checks ``required_env`` before opening anything.
    """

    name: str
    required_env: tuple[str, ...]
    open: Callable[[Path], AbstractContextManager[Arm]]
