"""The router: classify a question, then decline it or hand it to the mapped arm (Step 13).

The router implements ``Arm.run`` so the harness scores it like an arm, but it
is not one: it retrieves nothing itself (CONTEXT.md, router).  For each
question it sees only the question text:

1. ``retrieve.route_classifier`` classifies it with claude-haiku-5-5 inside a
   ``route`` span, the call its child.
2. ``decline``: the fixed decline sentence, status ``declined``, with no
   retrieval and no answer call.
3. Any other class: the arm the routing map names answers, and its result
   carries the routing record.

Inner arms are opened on first use and closed with the router.  A classifier
reply cut off, refused or off the schema, or a failed call, is an
``ArmError``; there is no default class.  ``eval.run --arm router --map
pre_registered|dev_best`` selects it; the dev-best map is read from its file
when the router opens.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType, TracebackType

from retrieve.answer import declined_without_call
from retrieve.answer_model import (
    ANSWER_MAX_TOKENS,
    AnswerModelError,
    AnthropicAnswerModel,
    Effort,
)
from retrieve.answer_model import API_KEY_ENV as ANTHROPIC_KEY_ENV
from retrieve.answer_prompt import PromptError, load_prompt
from retrieve.arm import (
    DECLINED,
    Arm,
    ArmConfig,
    ArmError,
    ArmResult,
    ArmSpec,
    EstimateQuestion,
    Retrieval,
    RouterConfig,
    Routing,
)
from retrieve.query_cache import CacheError
from retrieve.question_filter import QuestionFilter
from retrieve.response_cache import CachedAnswerModel
from retrieve.route_classifier import (
    ROUTE_EFFORT,
    ROUTE_MAX_TOKENS,
    ROUTE_MODEL,
    ROUTE_PROMPT_VERSION,
    Classification,
    RouteClassifier,
    RouteError,
    estimate_classification_usd,
)
from retrieve.route_prompt import RoutePromptError, load_route_prompt
from retrieve.routing_maps import (
    DEV_BEST_FILE,
    MAP_NAMES,
    ROUTED_CLASSES,
    RoutingMap,
    RoutingMapError,
    load_map,
)
from retrieve.tracing import ROUTE_SPAN, Kind, SpanRecorder, route_attributes

ROUTER_NAME = "router"
NO_FILTER = QuestionFilter((), (), (), None)  # a declined question is never filtered
_CLASSIFY_ERRORS = (RouteError, AnswerModelError, CacheError, OSError)


class Router:
    """Classifies each question and declines it or runs the arm *routing_map* names."""

    name = ROUTER_NAME

    def __init__(self, classifier: RouteClassifier, routing_map: RoutingMap,
                 arms: Mapping[str, ArmSpec], response_cache: Path,
                 spans: SpanRecorder | None = None):
        missing = sorted(set(routing_map.arms.values()) - set(arms))
        if missing:
            raise ArmError(f"{ROUTER_NAME}: the {routing_map.name} map names arms with no "
                           f"spec: {missing}")
        self._classifier = classifier
        self._map = routing_map
        self._specs = arms
        self._cache = response_cache
        self._spans = spans if spans is not None else SpanRecorder.off()
        self._opened: dict[str, Arm] = {}
        self._stack = ExitStack()
        try:
            self._answer_prompt = load_prompt()
        except PromptError as exc:
            raise ArmError(f"{ROUTER_NAME}: {exc}") from exc

    def __enter__(self) -> Router:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self._opened.clear()
        self._stack.close()

    @property
    def config(self) -> ArmConfig:
        """The router's settings and each arm opened so far; inner models and efforts are
        keyed ``{arm}.{role}``."""
        inner = {name: arm.config for name, arm in self._opened.items()}
        chunkers = sorted({c.chunker_version for c in inner.values()})
        prompt = self._classifier.prompt
        return ArmConfig(
            models=MappingProxyType({f"{a}.{role}": m for a, c in inner.items()
                                     for role, m in c.models.items()}),
            k=max((c.k for c in inner.values()), default=0),
            chunker_version=",".join(chunkers),
            efforts=MappingProxyType({f"{a}.{role}": e for a, c in inner.items()
                                      for role, e in c.efforts.items()}),
            answer_prompt=self._answer_prompt,
            answer_max_tokens=ANSWER_MAX_TOKENS,
            router=RouterConfig(
                model=ROUTE_MODEL, effort=self._classifier.effort, max_tokens=ROUTE_MAX_TOKENS,
                prompt_version=prompt.prompt_version, prompt_sha256=prompt.sha256,
                prompt_path=prompt.path, map_name=self._map.name, map=self._map.arms,
                map_sha256=self._map.sha256, map_file=self._map.file,
                arms=MappingProxyType(inner)),
        )

    def run(self, question: str) -> ArmResult:
        routing = self._route(question)
        if routing.declined:
            return ArmResult(**_no_retrieval(), answer=declined_without_call(), sources=(),
                             routing=routing)
        return replace(self._arm(routing.arm).run(question), routing=routing)

    def retrieve(self, question: str) -> Retrieval:
        routing = self._route(question)
        if routing.declined:
            return Retrieval(**_no_retrieval(), routing=routing)
        return replace(self._arm(routing.arm).retrieve(question), routing=routing)

    def _route(self, question: str) -> Routing:
        with self._spans.span(ROUTE_SPAN, Kind.CHAIN) as span:
            try:
                found = self._classifier.classify(question)
            except _CLASSIFY_ERRORS as exc:
                raise ArmError(f"{ROUTER_NAME}: {type(exc).__name__}: {exc}") from exc
            arm = (DECLINED if found.predicted_class == DECLINED
                   else self._map.arms[found.predicted_class])
            span.set_attributes(route_attributes(found.predicted_class, arm, self._map.name))
        return _routing(found, self._map.name, arm)

    def _arm(self, name: str) -> Arm:
        if name not in self._opened:
            self._opened[name] = self._stack.enter_context(
                self._specs[name].open(self._cache, self._spans))
        return self._opened[name]


def _routing(found: Classification, map_name: str, arm: str) -> Routing:
    return Routing(found.predicted_class, found.reason, map_name, arm, found.effort,
                   found.prompt_version, found.prompt_sha256, found.cost)


def _no_retrieval() -> dict[str, object]:
    return {"retrieved": (), "question_filter": NO_FILTER, "companies_without_chunks": (),
            "embed_ms": 0.0, "search_ms": 0.0, "query_cached": False, "embed_tokens": 0,
            "embed_cost_usd": 0.0}


def open_router(map_name: str, *, arms: Mapping[str, ArmSpec],
                dev_best_file: Path = DEV_BEST_FILE, effort: Effort = ROUTE_EFFORT,
                prompt_version: str = ROUTE_PROMPT_VERSION):
    """An ``ArmOpener`` for the router under *map_name*, with Claude and the response cache."""

    @contextmanager
    def opener(response_cache: Path, spans: SpanRecorder | None = None) -> Iterator[Router]:
        try:
            routing_map = load_map(map_name, dev_best_file)
            prompt = load_route_prompt(prompt_version)
        except (RoutingMapError, RoutePromptError) as exc:
            raise ArmError(f"{ROUTER_NAME}: {exc}") from exc
        with AnthropicAnswerModel.from_env() as claude:
            classifier = RouteClassifier(CachedAnswerModel(claude, response_cache), prompt,
                                         effort, spans)
            with Router(classifier, routing_map, arms, response_cache, spans) as router:
                yield router

    return opener


@dataclass(frozen=True)
class RouterEstimate:
    """Classification for every question, plus each question priced by the arm its gold
    class maps to (a decline question costs no answer; an unanswerable one the dearest
    arm's price), with nothing assumed to replay, as the arms' own estimates do."""

    map_name: str
    arms: Mapping[str, ArmSpec]
    dev_best_file: Path = DEV_BEST_FILE

    def __call__(self, questions: Sequence[EstimateQuestion], *, retrieval_only: bool) -> float:
        try:
            routing_map = load_map(self.map_name, self.dev_best_file)
            prompt = load_route_prompt(ROUTE_PROMPT_VERSION)
        except (RoutingMapError, RoutePromptError) as exc:
            raise ValueError(str(exc)) from exc
        total = estimate_classification_usd(prompt, len(questions))
        for arm in dict.fromkeys(routing_map.arms.values()):
            mine = [q for q in questions if routing_map.arms.get(q.class_) == arm]
            if mine:
                total += self.arms[arm].estimate(mine, retrieval_only=retrieval_only)
        others = [q for q in questions
                  if q.class_ not in ROUTED_CLASSES and q.class_ != DECLINED]
        if others:
            total += max(self.arms[a].estimate(others, retrieval_only=retrieval_only)
                         for a in dict.fromkeys(routing_map.arms.values()))
        return total


def router_spec(arms: Mapping[str, ArmSpec], dev_best_file: Path = DEV_BEST_FILE) -> ArmSpec:
    """The registry entry ``router``; ``with_map`` gives the spec ``eval.run`` runs."""
    required = tuple(dict.fromkeys([ANTHROPIC_KEY_ENV,
                                    *(e for s in arms.values() for e in s.required_env)]))

    def with_map(map_name: str) -> ArmSpec:
        if map_name not in MAP_NAMES:
            raise ValueError(f"unknown routing map {map_name!r}; choose from "
                             f"{', '.join(MAP_NAMES)}")
        return ArmSpec(name=ROUTER_NAME, required_env=required,
                       open=open_router(map_name, arms=arms, dev_best_file=dev_best_file),
                       estimate=RouterEstimate(map_name, arms, dev_best_file))

    def needs_map(*_args, **_kwargs):
        raise ArmError(f"{ROUTER_NAME} needs a routing map (--map {'|'.join(MAP_NAMES)})")

    return ArmSpec(name=ROUTER_NAME, required_env=required, open=needs_map,
                   estimate=needs_map, with_map=with_map)
