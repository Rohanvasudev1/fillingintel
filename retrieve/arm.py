"""The one interface every arm implements, so the harness runs any arm by name.

An arm receives the question text and nothing else from the eval record
(ADR-0001), so it can never see a record's labels.  Every arm reads its filter
with the shared parser in ``retrieve.question_filter``.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, get_args

from graph.ontology import Confidence
from retrieve.answer import Answer
from retrieve.answer_model import TokenUsage
from retrieve.answer_prompt import AnswerPrompt, SourceChunk
from retrieve.pricing import anthropic_cost
from retrieve.question_filter import QuestionFilter

if TYPE_CHECKING:
    from retrieve.tracing import SpanRecorder


class ArmError(RuntimeError):
    """An arm could not start or could not answer; the message is safe to print."""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    score: float
    confidence: Confidence | None = None  # graph arms: the chunk's best evidence confidence


AnchorMatch = Literal["name", "embedding"]
# Why a traversal question fell back to graph_local, after its query and one repair.
FallbackReason = Literal["invalid", "error", "timeout", "empty"]
# How one generated query ended: ``ok`` returned chunks; ``empty`` passed the guard and ran
# but returned none; the others failed the guard, raised an error or timed out.
CypherOutcome = Literal["ok", FallbackReason]
VALID_OUTCOMES: frozenset[CypherOutcome] = frozenset({"ok", "empty"})  # passed the guard, ran
GraphStage = Literal["anchor_linking", "cypher_generation", "cypher_repair", "cypher_execution"]
GRAPH_STAGES: tuple[GraphStage, ...] = get_args(GraphStage)


@dataclass(frozen=True)
class Anchor:
    """A graph node a question was linked to."""

    key: str
    label: str
    similarity: float  # 1.0 for a name match
    matched_by: AnchorMatch


@dataclass(frozen=True)
class CypherAttempt:
    """One generated query and how the guard and executor treated it."""

    query: str
    outcome: CypherOutcome
    rows: int = 0
    rule: str | None = None  # the guard rule or error category that stopped it
    message: str | None = None

    @property
    def valid(self) -> bool:
        return self.outcome in VALID_OUTCOMES


@dataclass(frozen=True)
class StageCost:
    """Latency, tokens and USD of one graph retrieval stage for one question."""

    ms: float
    cost_usd: float = 0.0
    input_tokens: int = 0  # for Claude, uncached plus cache reads and writes
    output_tokens: int = 0
    from_cache: bool = False

    @classmethod
    def anthropic(cls, ms: float, model: str, usage: TokenUsage, *,
                  from_cache: bool) -> StageCost:
        """A Claude call's stage, priced from the results header's price table.

        Raises ``ValueError`` for a model the table has no price for.
        """
        return cls(
            ms=ms,
            cost_usd=anthropic_cost(model, usage),
            input_tokens=(usage.input_tokens + usage.cache_read_input_tokens
                          + usage.cache_creation_input_tokens),
            output_tokens=usage.output_tokens,
            from_cache=from_cache,
        )


@dataclass(frozen=True)
class GraphRetrieval:
    """What a graph arm adds to a retrieval.

    ``graph_local`` fills the anchors and hop count; ``graph_traversal`` fills the
    Cypher attempts and, after a fallback to ``graph_local``, both.  ``stages``
    holds only the stages that ran, keyed by ``GRAPH_STAGES`` names.
    """

    stages: Mapping[GraphStage, StageCost] = field(default_factory=lambda: MappingProxyType({}))
    anchors: tuple[Anchor, ...] = ()
    hops: int | None = None
    no_anchor: bool = False
    cypher_attempts: tuple[CypherAttempt, ...] = ()
    fallback_reason: FallbackReason | None = None

    def __post_init__(self) -> None:
        unknown = sorted(set(self.stages) - set(GRAPH_STAGES))
        if unknown:
            raise ValueError(f"unknown graph stages {unknown}; expected {GRAPH_STAGES}")

    @property
    def first_pass_valid(self) -> bool | None:
        """The first query passed the guard and ran; None with no query."""
        return self.cypher_attempts[0].valid if self.cypher_attempts else None

    @property
    def valid_after_repair(self) -> bool | None:
        """Some query, the first or its one repair, passed the guard and ran."""
        return any(a.valid for a in self.cypher_attempts) if self.cypher_attempts else None

    @property
    def non_empty(self) -> bool | None:
        """Some query returned chunks."""
        return (any(a.outcome == "ok" for a in self.cypher_attempts)
                if self.cypher_attempts else None)


@dataclass(frozen=True)
class Retrieval:
    """What an arm's search returned for one question, before any answer is written."""

    retrieved: tuple[RetrievedChunk, ...]  # best first
    question_filter: QuestionFilter
    companies_without_chunks: tuple[str, ...]  # named companies no retrieved chunk came from
    embed_ms: float  # query embedding, from the API or the disk cache
    search_ms: float
    query_cached: bool  # cached embeddings take ~0 ms, so latency stats must tell them apart
    embed_tokens: int  # as Voyage reported them, also for a cached embedding
    embed_cost_usd: float
    # None for the vector arm.  For a graph arm, search_ms covers every stage after the
    # query embedding, so retrieval_ms stays comparable across arms.
    graph: GraphRetrieval | None = field(default=None, kw_only=True)

    @property
    def retrieval_ms(self) -> float:
        return self.embed_ms + self.search_ms


@dataclass(frozen=True)
class ArmResult(Retrieval):
    """What one arm returned for one question: its retrieval and the answer written from it."""

    answer: Answer
    sources: tuple[SourceChunk, ...]  # what the answer model saw, in retrieval order; for judges


@dataclass(frozen=True)
class GraphProvenance:
    """What a graph arm read, for the results header."""

    ontology_version: int  # from :GraphMeta, checked against the code
    schema_sha256: str
    load_run: str  # the graph load report the graph came from, e.g. "load-run4"
    cypher_prompt_version: str | None = None  # graph_traversal only, e.g. "cypher/v1@1a2b3c4d"
    cypher_prompt_sha256: str | None = None


@dataclass(frozen=True)
class ArmConfig:
    """What the results header records about an arm."""

    models: Mapping[str, str]  # role -> model ID, e.g. {"embedding": "voyage-4-large"}
    k: int
    chunker_version: str
    efforts: Mapping[str, str]  # role -> effort, e.g. {"answer": "high"}
    answer_prompt: AnswerPrompt
    answer_max_tokens: int
    graph: GraphProvenance | None = None  # None for the vector arm


class Arm(Protocol):
    name: str
    config: ArmConfig

    def run(self, question: str) -> ArmResult: ...


class ArmOpener(Protocol):
    """Opens an arm on the response cache folder its model calls go through.

    With a span recorder the arm records its steps as spans; with none it records nothing.
    """

    def __call__(self, response_cache: Path, spans: SpanRecorder | None = None,
                 ) -> AbstractContextManager[Arm]: ...


@dataclass(frozen=True)
class ArmSpec:
    """A registered arm: the environment variables it needs and how to open it.

    ``open`` returns a context manager, so the arm owns and closes its own
    connections.  The harness checks ``required_env`` before opening anything.
    """

    name: str
    required_env: tuple[str, ...]
    open: ArmOpener
