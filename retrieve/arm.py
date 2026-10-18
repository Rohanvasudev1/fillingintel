"""The one interface every arm implements, so the harness runs any arm by name.

An arm receives the question text and nothing else from the eval record
(ADR-0001), so it can never see a record's labels.  Every arm reads its filter
with the shared parser in ``retrieve.question_filter``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, get_args

from graph.ontology import Confidence
from retrieve.answer import Answer
from retrieve.answer_model import TokenUsage
from retrieve.answer_prompt import AnswerPrompt, SourceChunk
from retrieve.pricing import TABLE_2026_10_05, PriceTable, anthropic_cost
from retrieve.question_filter import QuestionFilter
from retrieve.route_prompt import DECLINE

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
# graph_global (Step 12) adds community_select and global_map.  Its reduce call writes the
# answer, so its cost and latency are the answer's ("generation"), not a stage.
GraphStage = Literal["anchor_linking", "cypher_generation", "cypher_repair", "cypher_execution",
                     "community_select", "global_map"]
GRAPH_STAGES: tuple[GraphStage, ...] = get_args(GraphStage)
# Each arm family reports its own stages, so adding graph_global left the files of the
# entity arms (graph_local, graph_traversal) unchanged.
ENTITY_STAGES: tuple[GraphStage, ...] = ("anchor_linking", "cypher_generation", "cypher_repair",
                                         "cypher_execution")
GLOBAL_STAGES: tuple[GraphStage, ...] = ("community_select", "global_map")


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
                  from_cache: bool, table: PriceTable = TABLE_2026_10_05) -> StageCost:
        """A Claude call's stage, priced from *table*: the results header's price table
        unless the caller names another (the router's classifier, priced 2026-10-10).

        Raises ``ValueError`` for a model the table has no price for.
        """
        return cls(
            ms=ms,
            cost_usd=anthropic_cost(model, usage, table),
            input_tokens=(usage.input_tokens + usage.cache_read_input_tokens
                          + usage.cache_creation_input_tokens),
            output_tokens=usage.output_tokens,
            from_cache=from_cache,
        )


# Why a map point was left out before the reduce step; how one map call's reply ended.
MapDropReason = Literal["no_citation", "citation_not_shown", "bad_score"]
MapOutcome = Literal["ok", "invalid_reply", "refused", "max_tokens"]


@dataclass(frozen=True)
class MapPoint:
    """One statement a map call wrote, with its score and whether it was kept."""

    community_id: str
    text: str
    score: int | None  # None when the reply's score was not an integer from 0 to 100
    citations: tuple[str, ...]
    drop_reason: MapDropReason | None = None
    in_reduce: bool = False  # sent to the reduce call

    @property
    def kept(self) -> bool:
        return self.drop_reason is None


@dataclass(frozen=True)
class MapCall:
    """One selected community and what its map call returned."""

    community_id: str
    rank: int  # 1 for the best
    similarity: float  # cosine between the question and the community's report
    shown: tuple[str, ...]  # the chunk IDs the call was shown, best first
    outcome: MapOutcome
    points: tuple[MapPoint, ...]
    cost: StageCost  # ms is the call's API latency when it was made


@dataclass(frozen=True)
class GlobalSearch:
    """What graph_global's map and reduce steps did for one question."""

    calls: tuple[MapCall, ...]  # by community rank
    reduce_points: int  # points sent to the reduce call; 0 when none scored above 0
    reduce_input_tokens: int  # their cl100k_base count, as the budget measures it
    cut_by_budget: int  # points above 0 left out by the reduce budget

    @property
    def points(self) -> tuple[MapPoint, ...]:
        return tuple(p for c in self.calls for p in c.points)

    @property
    def reduce_called(self) -> bool:
        return self.reduce_points > 0


@dataclass(frozen=True)
class GraphRetrieval:
    """What a graph arm adds to a retrieval.

    ``graph_local`` fills the anchors and hop count; ``graph_traversal`` fills the
    Cypher attempts and, after a fallback to ``graph_local``, both; ``graph_global``
    fills its map and reduce steps.  ``stages``
    holds only the stages that ran, keyed by ``GRAPH_STAGES`` names.
    """

    stages: Mapping[GraphStage, StageCost] = field(default_factory=lambda: MappingProxyType({}))
    anchors: tuple[Anchor, ...] = ()
    hops: int | None = None
    no_anchor: bool = False
    cypher_attempts: tuple[CypherAttempt, ...] = ()
    fallback_reason: FallbackReason | None = None
    global_search: GlobalSearch | None = None  # graph_global only

    def __post_init__(self) -> None:
        unknown = sorted(set(self.stages) - set(GRAPH_STAGES))
        if unknown:
            raise ValueError(f"unknown graph stages {unknown}; expected {GRAPH_STAGES}")

    @property
    def stage_names(self) -> tuple[GraphStage, ...]:
        """The stages this arm family reports, in ``GRAPH_STAGES`` order."""
        return GLOBAL_STAGES if self.global_search is not None else ENTITY_STAGES

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


DECLINED = DECLINE  # the router's "arm" for a question it declined itself


@dataclass(frozen=True)
class Routing:
    """How the router handled one question (Step 13): its predicted class, why, the map,
    the arm it chose (or ``decline``), the classifier's settings and what the call cost."""

    predicted_class: str
    reason: str
    map_name: str
    arm: str  # an arm name, or DECLINED
    effort: str
    prompt_version: str  # e.g. "route/v1@1a2b3c4d"
    prompt_sha256: str
    cost: StageCost  # the classifier call; ms is its API latency when it was made

    @property
    def declined(self) -> bool:
        return self.arm == DECLINED


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
    routing: Routing | None = field(default=None, kw_only=True)  # router runs only

    @property
    def retrieval_ms(self) -> float:
        return self.embed_ms + self.search_ms


@dataclass(frozen=True)
class ArmResult(Retrieval):
    """What one arm returned for one question: its retrieval and the answer written from it."""

    answer: Answer
    sources: tuple[SourceChunk, ...]  # what the answer model saw, in retrieval order; for judges


@dataclass(frozen=True)
class GlobalSearchConfig:
    """graph_global's frozen settings and the files it read, for the results header."""

    n: int  # communities selected per question
    level: str  # the community level selected from
    chunks_per_community: int
    reduce_budget_tokens: int
    map_workers: int
    map_prompt_version: str  # e.g. "global_map/v1@1a2b3c4d"
    map_prompt_sha256: str
    reduce_prompt_version: str
    reduce_prompt_sha256: str
    report_prompt_versions: tuple[str, ...]  # those of the reports the graph holds
    communities_file: str  # e.g. "communities-run1.json"
    reports_file: str  # e.g. "reports-run2.jsonl"


@dataclass(frozen=True)
class GraphProvenance:
    """What a graph arm read, for the results header."""

    ontology_version: int  # from :GraphMeta, checked against the code
    schema_sha256: str
    load_run: str  # the graph load report the graph came from, e.g. "load-run4"
    cypher_prompt_version: str | None = None  # graph_traversal only, e.g. "cypher/v1@1a2b3c4d"
    cypher_prompt_sha256: str | None = None
    global_search: GlobalSearchConfig | None = None  # graph_global only


@dataclass(frozen=True)
class RouterConfig:
    """What the results header records about the router (Step 13)."""

    model: str
    effort: str
    max_tokens: int
    prompt_version: str
    prompt_sha256: str
    prompt_path: str
    map_name: str
    map: Mapping[str, str]  # predicted class -> arm
    map_sha256: str
    map_file: str | None  # the dev-best map's file; None for the pre-registered map
    arms: Mapping[str, ArmConfig]  # each arm the router opened, by name


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
    router: RouterConfig | None = None  # the router only


class Arm(Protocol):
    name: str
    config: ArmConfig

    def run(self, question: str) -> ArmResult: ...

    def retrieve(self, question: str) -> Retrieval:
        """The retrieval ``run`` would answer from, with no answer model call."""
        ...


@dataclass(frozen=True)
class EstimateQuestion:
    """What a cost estimate reads of a selected eval record: its ID and class, never its
    text or labels, so an estimate cannot steer a run."""

    id: str
    class_: str


class ArmEstimator(Protocol):
    """The estimated USD of running an arm on *questions*, before it opens.

    With *retrieval_only*, only the retrieval calls are priced; otherwise retrieval,
    answer and judge calls.  Raises ``ValueError`` when it cannot estimate.
    """

    def __call__(self, questions: Sequence[EstimateQuestion], *,
                 retrieval_only: bool) -> float: ...


class ArmOpener(Protocol):
    """Opens an arm on the response cache folder its model calls go through.

    With a span recorder the arm records its steps as spans; with none it records nothing.
    """

    def __call__(self, response_cache: Path, spans: SpanRecorder | None = None,
                 ) -> AbstractContextManager[Arm]: ...


@dataclass(frozen=True)
class ArmSpec:
    """A registered arm: the environment variables it needs, how to open it and how to
    estimate a run's cost.

    ``open`` returns a context manager, so the arm owns and closes its own
    connections.  The harness checks ``required_env`` and prints ``estimate``
    before opening anything.
    """

    name: str
    required_env: tuple[str, ...]
    open: ArmOpener
    estimate: ArmEstimator
    # The router only: the spec for one routing map (``eval.run --map``).
    with_map: Callable[[str], ArmSpec] | None = None
