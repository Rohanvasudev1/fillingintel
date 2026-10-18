"""Stand-in arms and a scripted classifier model for the router tests (Step 13 ticket 03).

A stand-in arm records each question it receives and returns one retrieved chunk
and a recorded answer; its spec counts opens and closes.  The scripted model
answers each classifier request with the class the test gives its question.
"""
from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType

from retrieve.answer import read_answer
from retrieve.answer_model import ApiResponse
from retrieve.answer_prompt import load_prompt
from retrieve.arm import ArmConfig, ArmResult, ArmSpec, Retrieval, RetrievedChunk
from retrieve.question_filter import QuestionFilter
from retrieve.route_classifier import RouteRequest
from tests.anthropic_fixtures import load, with_text

CHUNK = "0001045810-26-000021:0067"  # a real NVIDIA FY2026 10-K chunk
NO_FILTER = QuestionFilter((), (), (), None)
ARM_NAMES = ("vector", "graph_local", "graph_traversal", "graph_global")
QUESTIONS = {  # invented questions, one per predicted class; never eval questions
    "lookup": "What was AMD's diluted earnings per share in fiscal 2024?",
    "local": "How does Intel describe the risks of its foundry customer commitments?",
    "multi_hop": "How did NVIDIA's and AMD's R&D spending change between their last two 10-Ks?",
    "global": "What themes about AI demand recur across all three companies' filings?",
    "decline": "Should I sell my Intel shares before the next earnings report?",
}
# The dev records tests.test_eval_run uses, whose recorded classifier replies test eval.route.
DEV_IDS = ("q0072", "q0073", "q0113", "q0028", "q0004", "q0011")
FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"


def class_reply(predicted_class: str, reason: str = "Scripted.") -> dict:
    """A recorded Haiku-shaped body whose text is the classifier reply."""
    return with_text(load("answered_q0072")["response"],
                     json.dumps({"reason": reason, "predicted_class": predicted_class}))


class ScriptedRouteModel:
    """Answers each classifier request with the body *bodies* gives its question."""

    def __init__(self, bodies: Mapping[str, dict], from_cache: bool = False):
        self._bodies = bodies
        self._from_cache = from_cache
        self.requests: list[RouteRequest] = []

    def complete(self, request: RouteRequest) -> ApiResponse:
        self.requests.append(request)
        question = request.user.removeprefix("Question: ")
        return ApiResponse(self._bodies[question], api_ms=250.0, from_cache=self._from_cache)


class StandInArm:
    def __init__(self, name: str):
        self.name = name
        self.questions: list[str] = []
        self.retrieved_only: list[str] = []
        self.config = ArmConfig(
            models=MappingProxyType({"answer": "claude-sonnet-5-5"}), k=10,
            chunker_version="chunker-test", efforts=MappingProxyType({"answer": "high"}),
            answer_prompt=load_prompt("v1"), answer_max_tokens=16_000)

    def _retrieval(self) -> Retrieval:
        return Retrieval((RetrievedChunk(CHUNK, 0.9),), NO_FILTER, (), embed_ms=5.0,
                         search_ms=7.0, query_cached=True, embed_tokens=12, embed_cost_usd=0.0)

    def retrieve(self, question: str) -> Retrieval:
        self.retrieved_only.append(question)
        return self._retrieval()

    def run(self, question: str) -> ArmResult:
        self.questions.append(question)
        body = with_text(load("answered_q0072")["response"], f"STATUS: answered\nA fact [{CHUNK}].")
        answer = read_answer(ApiResponse(body, api_ms=900.0), {CHUNK})
        r = self._retrieval()
        return ArmResult(r.retrieved, r.question_filter, r.companies_without_chunks, r.embed_ms,
                         r.search_ms, r.query_cached, r.embed_tokens, r.embed_cost_usd,
                         answer=answer, sources=())


class StandInArms:
    """One stand-in arm per name, with specs that count opens and closes."""

    def __init__(self, names: Sequence[str] = ARM_NAMES):
        self.arms = {n: StandInArm(n) for n in names}
        self.opened: list[str] = []
        self.closed: list[str] = []

    def spec(self, name: str) -> ArmSpec:
        @contextmanager
        def open_arm(response_cache: Path, spans=None) -> Iterator[StandInArm]:
            self.opened.append(name)
            try:
                yield self.arms[name]
            finally:
                self.closed.append(name)

        return ArmSpec(name=name, required_env=(f"{name.upper()}_ENV",), open=open_arm,
                       estimate=lambda questions, *, retrieval_only: 0.01 * len(questions))

    @property
    def specs(self) -> dict[str, ArmSpec]:
        return {n: self.spec(n) for n in self.arms}


class RecordedRouteModel:
    """Replays ``route_{name}.json`` by request hash; an unrecorded request fails, so a
    request built differently from the recorded one fails the test."""

    def __init__(self, name: str):
        fixture = json.loads((FIXTURES / f"route_{name}.json").read_text(encoding="utf-8"))
        self.prompt_version = fixture["prompt_version"]
        self.effort = fixture["effort"]
        self.calls = fixture["calls"]
        self._bodies = {c["request_sha256"]: (c["response"], c["api_ms"]) for c in self.calls}
        self.requests: list[RouteRequest] = []

    def complete(self, request: RouteRequest) -> ApiResponse:
        self.requests.append(request)
        body, api_ms = self._bodies[request.cache_key()]
        return ApiResponse(body, api_ms=api_ms, from_cache=True)
