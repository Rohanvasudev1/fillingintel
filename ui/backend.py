"""The UI backend: everything the Streamlit app asks of the system (Step 14).

``Backend`` is the seam the app is given at start; the app's tests pass a fake.
``LiveBackend`` answers a question through one picker option by opening the
option's arm with the existing ``ArmSpec.open`` (routers through ``with_map``),
once per option for the app's life, and calling ``Arm.run``.  It fetches a
cited chunk's source passage, lists the committed sample questions and reads a
sample's committed dev metrics.

Before any call it checks the option's settings and, for a live question, the
session's spend cap.  After opening it checks every model has a price.  A
missing setting, an unpriced model, an unreachable database or an arm failure
comes back as a ``BackendError`` in plain words.  With
``PHOENIX_COLLECTOR_ENDPOINT`` set, each question is one trace rooted in an
``answer_question`` span; unset, no tracing SDK object is built.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Collection, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import psycopg
from neo4j.exceptions import DriverError, Neo4jError

from eval.operational import question_cost, replayed
from retrieve.arm import Arm, ArmError, ArmResult, ArmSpec
from retrieve.arms import ARMS
from retrieve.pricing import price_table
from retrieve.response_cache import DEFAULT_CACHE_DIR
from retrieve.router import Router
from retrieve.tracing import (
    QUESTION_SPAN,
    Kind,
    SpanRecorder,
    build_provider,
    question_attributes,
    status_attributes,
    text_capture,
)
from ui.committed import SampleMetrics, mean_dev_cost, sample_metrics
from ui.communities import CommunityInfo
from ui.errors import (
    AnswerFailed,
    DatabaseUnavailable,
    SettingsMissing,
    SourceNotFound,
    UnpricedModel,
)
from ui.options import PickerOption, option_spec, picker_option
from ui.samples import Sample, Samples, read_samples
from ui.source import SourcePassage
from ui.spend import check_spend, fresh_cost, max_spend

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

TRACER_NAME = "filingintel.ui"
_MS_PER_SECOND = 1000.0
_NEO4J_DOWN = ("Neo4j stopped answering. Start it with docker compose up -d, then ask again.")
_POSTGRES_DOWN = ("Postgres stopped answering. Start it with docker compose up -d, then ask "
                  "again.")


@dataclass(frozen=True)
class UiAnswer:
    """One question answered through one picker option, with what the page shows."""

    option: str  # the picker label
    question: str
    result: ArmResult  # the answer, its sentences and the arm's trace fields
    total_ms: float  # wall time of the whole arm call
    cost: Mapping[str, float]  # eval.operational.question_cost: USD per stage and total
    spent_usd: float  # what this call added to the session: calls replayed cost nothing
    replayed: bool  # any call replayed from a cache
    is_sample: bool  # a committed sample question for this option
    trace_id: str | None = None  # the Phoenix trace, when tracing is on
    communities: Mapping[str, CommunityInfo] = field(default_factory=dict)  # graph_global


class Backend(Protocol):
    """What the app asks of the system; every failure is a ``BackendError``."""

    @property
    def max_spend(self) -> float: ...

    def answer(self, question: str, option: str, *, spent_usd: float) -> UiAnswer: ...

    def estimate(self, option: str) -> float: ...

    def source(self, chunk_id: str) -> SourcePassage: ...

    def samples(self, option: str) -> tuple[Sample, ...]: ...

    def chips(self) -> tuple[Sample, ...]: ...

    def sample_metrics(self, option: str, question_id: str) -> SampleMetrics | None: ...


Probe = Callable[[ArmSpec], None]  # raises DatabaseUnavailable
CommunityReader = Callable[[Collection[str]], Mapping[str, CommunityInfo]]
SourceReader = Callable[[str], SourcePassage]


class LiveBackend:
    """The backend over the real arms, the response cache and the committed files.

    Safe to share between Streamlit sessions: each option's arm answers one
    question at a time.  ``close`` closes every arm it opened.
    """

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        arms: Mapping[str, ArmSpec] = ARMS,
        response_cache: Path | None = None,
        samples: Callable[[], Samples] = read_samples,
        probe: Probe | None = None,
        tracing: Callable[[Mapping[str, str]], TracerProvider | None] = build_provider,
        communities: CommunityReader | None = None,
        source_reader: SourceReader | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._environ = dict(os.environ if environ is None else environ)
        self._arms = arms
        self._response_cache = response_cache or DEFAULT_CACHE_DIR
        self._read_samples = samples
        self._samples: Samples | None = None
        self._probe = probe or probe_databases(self._environ)
        self._communities = communities or _unavailable("communities")
        self._source_reader = source_reader or _unavailable("source passages")
        self._clock = clock
        self._max_spend = max_spend(self._environ)
        self._provider = tracing(self._environ)
        self._spans = (SpanRecorder.off() if self._provider is None else
                       SpanRecorder(self._provider.get_tracer(TRACER_NAME),
                                    text_capture(self._environ)))
        self._open: dict[str, tuple[ExitStack, Arm, threading.Lock]] = {}
        self._opening = threading.Lock()

    @property
    def max_spend(self) -> float:
        return self._max_spend

    def answer(self, question: str, option: str, *, spent_usd: float) -> UiAnswer:
        picked = picker_option(option)
        spec = option_spec(picked, self._arms)
        _check_settings(spec, self._environ)
        is_sample = question in {s.question for s in self.samples(option)}
        if not is_sample:
            check_spend(estimate=self.estimate(option), spent=spent_usd, cap=self._max_spend)
        arm, lock = self._arm(picked, spec)
        with lock:
            result, total_ms, trace_id = self._run(arm, question, picked)
        return UiAnswer(
            option=option, question=question, result=result, total_ms=total_ms,
            cost=question_cost(result), spent_usd=fresh_cost(result),
            replayed=replayed(result), is_sample=is_sample, trace_id=trace_id,
            communities=self._read_communities(result))

    def estimate(self, option: str) -> float:
        return mean_dev_cost(picker_option(option))

    def source(self, chunk_id: str) -> SourcePassage:
        return self._source_reader(chunk_id)

    def samples(self, option: str) -> tuple[Sample, ...]:
        return self._committed_samples().options.get(picker_option(option).label, ())

    def chips(self) -> tuple[Sample, ...]:
        return self._committed_samples().chips

    def sample_metrics(self, option: str, question_id: str) -> SampleMetrics | None:
        return sample_metrics(picker_option(option), question_id)

    def close(self) -> None:
        with self._opening:
            for stack, _, _ in self._open.values():
                stack.close()
            self._open.clear()
        if self._provider is not None:
            self._provider.shutdown()

    def _committed_samples(self) -> Samples:
        if self._samples is None:
            self._samples = self._read_samples()
        return self._samples

    def _arm(self, option: PickerOption, spec: ArmSpec) -> tuple[Arm, threading.Lock]:
        with self._opening:
            if option.label not in self._open:
                self._probe(spec)
                self._open[option.label] = self._open_arm(spec)
            _, arm, lock = self._open[option.label]
        return arm, lock

    def _open_arm(self, spec: ArmSpec) -> tuple[ExitStack, Arm, threading.Lock]:
        stack = ExitStack()
        try:
            arm = stack.enter_context(spec.open(self._response_cache, self._spans))
            if isinstance(arm, Router):
                arm.open_mapped()  # every mapped arm checked before the first question
            _check_prices(arm)
        except ArmError as exc:
            stack.close()
            raise AnswerFailed(str(exc)) from exc
        except UnpricedModel:
            stack.close()
            raise
        return stack, arm, threading.Lock()

    def _run(self, arm: Arm, question: str,
             option: PickerOption) -> tuple[ArmResult, float, str | None]:
        attributes = question_attributes(question, {"option": option.label, "arm": arm.name})
        try:
            with self._spans.span(QUESTION_SPAN, Kind.CHAIN, attributes) as span:
                start = self._clock()
                result = arm.run(question)
                total_ms = (self._clock() - start) * _MS_PER_SECOND
                span.set_attributes(status_attributes(result.answer.status))
        except ArmError as exc:
            raise AnswerFailed(str(exc)) from exc
        except (Neo4jError, DriverError) as exc:
            self._drop(option)
            raise DatabaseUnavailable(_NEO4J_DOWN) from exc
        except psycopg.OperationalError as exc:
            self._drop(option)
            raise DatabaseUnavailable(_POSTGRES_DOWN) from exc
        trace_id = span.get_span_context().trace_id
        return result, total_ms, (f"{trace_id:032x}" if trace_id else None)

    def _drop(self, option: PickerOption) -> None:
        """Forget *option*'s arm after a lost connection, so the next question reopens it."""
        with self._opening:
            opened = self._open.pop(option.label, None)
        if opened is not None:
            try:
                opened[0].close()
            except (Neo4jError, DriverError, psycopg.Error):
                pass  # the connection is already gone; closing it again can fail

    def _read_communities(self, result: ArmResult) -> Mapping[str, CommunityInfo]:
        search = result.graph.global_search if result.graph is not None else None
        if search is None or not search.calls:
            return {}
        try:
            return self._communities(tuple(c.community_id for c in search.calls))
        except (Neo4jError, DriverError) as exc:
            raise DatabaseUnavailable(_NEO4J_DOWN) from exc


def _check_settings(spec: ArmSpec, environ: Mapping[str, str]) -> None:
    missing = [name for name in spec.required_env if not environ.get(name, "").strip()]
    if missing:
        raise SettingsMissing(f"Set {', '.join(missing)} in .env (see .env.example) and "
                              "restart the app.")


def _check_prices(arm: Arm) -> None:
    models = dict(arm.config.models)
    router = arm.config.router
    if router is not None:
        models |= {f"{name}.{role}": model for name, config in router.arms.items()
                   for role, model in config.models.items()}
    try:
        price_table(models.values())
    except ValueError as exc:
        raise UnpricedModel(f"The cost of this option cannot be shown: {exc}") from exc


def _unavailable(what: str) -> Callable[..., object]:
    def refuse(*_args: object) -> object:
        raise SourceNotFound(f"this backend reads no {what}")

    return refuse


def probe_databases(environ: Mapping[str, str]) -> Probe:
    """A probe that checks the databases *spec* needs answer, before its arm opens."""

    def probe(spec: ArmSpec) -> None:
        if "DATABASE_URL" in spec.required_env:
            _probe_postgres(environ)
        if "NEO4J_URI" in spec.required_env:
            _probe_neo4j(environ)

    return probe


def _probe_postgres(environ: Mapping[str, str]) -> None:
    try:
        psycopg.connect(environ.get("DATABASE_URL", ""), connect_timeout=3).close()
    except psycopg.Error as exc:  # the message can quote the URL: never shown
        raise DatabaseUnavailable("Postgres is not reachable at DATABASE_URL. Start it with "
                                  "docker compose up -d.") from exc


def _probe_neo4j(environ: Mapping[str, str]) -> None:
    from graph.connection import GraphSettingsMissing, driver_from_env

    try:
        with driver_from_env() as driver:
            driver.verify_connectivity()
    except GraphSettingsMissing as exc:
        raise SettingsMissing(f"{exc}; set it in .env and restart the app.") from exc
    except (Neo4jError, DriverError, OSError) as exc:
        raise DatabaseUnavailable("Neo4j is not reachable at NEO4J_URI. Start it with "
                                  "docker compose up -d.") from exc
