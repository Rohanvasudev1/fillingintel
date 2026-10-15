"""Recorded community report replies, for the report tests (Step 12).

Four replies recorded once by scripts/capture_report_responses.py with prompt
report/v1: three lower communities of Intel's top community
top-36ff66bed99f, then that top community's report from theirs.
"""
from __future__ import annotations

import json

from graph.community import Community
from graph.report_input import LowerInput, Member, MemberEdge, Passage
from retrieve.answer_model import AnswerModelError, ApiResponse
from tests.anthropic_fixtures import FIXTURES

RECORDED_UNDER = "report/v1@d296f30d"
TOP_ID = "top-36ff66bed99f"
LOWER_IDS = ("lower-5a541eb7341f", "lower-ac71ab119f3f", "lower-cb23d1979aad")


def load_report(community_id: str) -> dict:
    return json.loads((FIXTURES / f"report_{community_id}.json").read_text(encoding="utf-8"))


def community(recorded: dict) -> Community:
    c = recorded["community"]
    return Community(c["community_id"], c["level"], tuple(c["members"]), c["member_hash"],
                     c["parent"], c["reportable"])


def lower_input(recorded: dict) -> LowerInput:
    data = recorded["lower_input"]
    return LowerInput(
        data["community_id"],
        tuple(Member(**m) for m in data["members"]),
        tuple(MemberEdge(e["type"], e["start"], e["end"], tuple(e["chunk_ids"]),
                         tuple(e["spans"])) for e in data["edges"]),
        tuple(Passage(**p) for p in data["passages"]),
        frozenset(data["evidence"]),
    )


class Replay:
    """Serves each recorded body for its community; any other fails as an API error would.
    *bodies* replaces the recorded body of a community."""

    def __init__(self, bodies: dict[str, dict] | None = None):
        self._bodies = {cid: load_report(cid)["response"] for cid in (*LOWER_IDS, TOP_ID)}
        self._bodies |= bodies or {}
        self.calls: list[str] = []

    def complete(self, request):
        self.calls.append(request.community_id)
        body = self._bodies.get(request.community_id)
        if body is None:
            raise AnswerModelError("no recorded reply for this community")
        return ApiResponse(body=body, api_ms=1.0, from_cache=False)


def reply_json(body: dict) -> dict:
    return json.loads("".join(b["text"] for b in body["content"] if b["type"] == "text"))
