"""Write a checked, cited report for every reportable community (Step 12).

Lower communities are written first, *workers* calls at a time, then top
communities from their lower reports. Each call goes through the model it is
given (the cached Anthropic client in a run); its reply is parsed and passed
through the citation check (graph.report_check).

- A call that raises AnswerModelError fails the community (`call_failed`); a
  rerun tries again. Any other error (the response cache) raises.
- A reply cut off or refused, or off the schema, fails it (`invalid_reply`).
- A top community fails (`lower_report_failed`) when one of its reportable
  lower communities failed, since its input would be incomplete.
- A top community whose lower reports kept no text gets an empty report with
  no call (status `no_input`), so every reportable community has a record.

Records and failures keep the communities' order.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, TypeVar

from graph.community import LOWER, TOP, Community, Level
from graph.report_check import CheckedReport, ReplyError, check_report, parse_report_reply
from graph.report_input import (
    DEFAULT_BUDGET,
    ChildReport,
    CommunityIndex,
    InputBudget,
    lower_input,
    top_input,
)
from graph.report_prompt import ReportPrompt
from graph.report_request import (
    REPORT_PRICES,
    ReportRequest,
    build_lower_request,
    build_top_request,
)
from retrieve.answer_model import AnswerModel, AnswerModelError, TokenUsage, parse_reply
from retrieve.pricing import anthropic_cost

WORKERS = 4
_UNUSABLE_STOPS = frozenset({"max_tokens", "refusal"})
Status = Literal["written", "no_input"]
Reason = Literal["call_failed", "invalid_reply", "lower_report_failed"]
T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class ReportRecord:
    community_id: str
    member_hash: str
    level: Level
    status: Status
    report: CheckedReport | None  # None for `no_input`
    inputs: tuple[str, ...]  # passages shown (lower) or lower reports read (top)
    prompt_version: str
    usage: TokenUsage | None
    cost_usd: float  # what the call cost when it was made
    from_cache: bool

    @property
    def text(self) -> str:
        return self.report.text if self.report is not None else ""

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        return self.report.report_chunk_ids if self.report is not None else ()

    def child(self) -> ChildReport:
        title = self.report.title if self.report is not None else ""
        return ChildReport(self.community_id, title, self.text, self.chunk_ids)


@dataclass(frozen=True, slots=True)
class ReportFailure:
    community_id: str
    level: Level
    reason: Reason
    detail: str
    usage: TokenUsage | None = None
    from_cache: bool = False


Outcome = ReportRecord | ReportFailure


@dataclass(frozen=True, slots=True)
class Planned:
    """A community, the request for it and the chunks its report may cite."""

    community: Community
    request: ReportRequest
    evidence: frozenset[str]
    inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportsResult:
    lower: tuple[Outcome, ...]
    top: tuple[Outcome, ...]

    @property
    def records(self) -> tuple[ReportRecord, ...]:
        return tuple(o for o in (*self.lower, *self.top) if isinstance(o, ReportRecord))

    @property
    def failures(self) -> tuple[ReportFailure, ...]:
        return tuple(o for o in (*self.lower, *self.top) if isinstance(o, ReportFailure))


def reportable(communities: Sequence[Community], level: Level) -> list[Community]:
    return [c for c in communities if c.level == level and c.reportable]


def plan_lower(communities: Sequence[Community], index: CommunityIndex,
               texts: Mapping[str, str], prompt: ReportPrompt,
               budget: InputBudget = DEFAULT_BUDGET) -> list[Planned]:
    """The request for every reportable lower community; ValueError for a missing text."""
    plans = []
    for community in reportable(communities, LOWER):
        built = lower_input(community, index, texts, budget)
        plans.append(Planned(community, build_lower_request(prompt, built), built.evidence,
                             tuple(p.chunk_id for p in built.passages)))
    return plans


def plan_top(communities: Sequence[Community], lower: Sequence[Outcome],
             prompt: ReportPrompt) -> list[Planned | Outcome]:
    """Per reportable top community: its request, or the outcome it gets with no call."""
    by_id = {o.community_id: o for o in lower}
    plans: list[Planned | Outcome] = []
    for community in reportable(communities, TOP):
        children = [by_id[c.community_id] for c in reportable(communities, LOWER)
                    if c.parent == community.community_id]
        if failed := [c.community_id for c in children if isinstance(c, ReportFailure)]:
            plans.append(ReportFailure(community.community_id, TOP, "lower_report_failed",
                                       f"lower reports failed: {', '.join(failed)}"))
            continue
        built = top_input(community, [c.child() for c in children
                                      if isinstance(c, ReportRecord)])
        if not built.children:
            plans.append(ReportRecord(community.community_id, community.member_hash, TOP,
                                      "no_input", None, (), prompt.prompt_version, None, 0.0,
                                      False))
            continue
        plans.append(Planned(community, build_top_request(prompt, built), built.evidence,
                             tuple(c.community_id for c in built.children)))
    return plans


class ReportWriter:
    """Writes one report per planned community through *model*."""

    def __init__(self, model: AnswerModel, prompt: ReportPrompt, workers: int = WORKERS):
        self._model = model
        self._prompt = prompt
        self._workers = workers

    def write_all(self, communities: Sequence[Community], index: CommunityIndex,
                  texts: Mapping[str, str], budget: InputBudget = DEFAULT_BUDGET
                  ) -> ReportsResult:
        lower = self.write_planned(plan_lower(communities, index, texts, self._prompt, budget))
        top = self.write_planned(plan_top(communities, lower, self._prompt))
        return ReportsResult(tuple(lower), tuple(top))

    def write_planned(self, plans: Sequence[Planned | Outcome]) -> list[Outcome]:
        """An outcome per plan, in order; a plan that is already an outcome is kept."""
        return _map(lambda p: self.write(p) if isinstance(p, Planned) else p, plans,
                    self._workers)

    def write(self, plan: Planned) -> Outcome:
        """The report for *plan*; an error not about this community (the cache) raises."""
        community, level = plan.community, plan.request.level
        try:
            response = self._model.complete(plan.request)
        except AnswerModelError as exc:
            return ReportFailure(community.community_id, level, "call_failed", str(exc))
        try:
            reply = parse_reply(response.body)
        except AnswerModelError as exc:
            return ReportFailure(community.community_id, level, "invalid_reply", str(exc),
                                 None, response.from_cache)
        if reply.stop_reason in _UNUSABLE_STOPS:
            return ReportFailure(community.community_id, level, "invalid_reply",
                                 f"the reply stopped with {reply.stop_reason}", reply.usage,
                                 response.from_cache)
        try:
            parsed = parse_report_reply(reply.text)
        except ReplyError as exc:
            return ReportFailure(community.community_id, level, "invalid_reply", str(exc),
                                 reply.usage, response.from_cache)
        return ReportRecord(
            community.community_id, community.member_hash, level, "written",
            check_report(parsed, plan.evidence), plan.inputs, self._prompt.prompt_version,
            reply.usage, anthropic_cost(plan.request.model, reply.usage, REPORT_PRICES),
            response.from_cache)


def _map(fn: Callable[[T], R], items: Sequence[T], workers: int) -> list[R]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))
