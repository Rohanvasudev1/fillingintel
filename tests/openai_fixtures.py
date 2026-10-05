"""Responses API bodies for the judge tests, built from a recorded ``gpt-6-luna`` reply.

``RECORDED`` is one real response from tests/fixtures/judge/responses/, so the
fakes return the shape the API returns; the helpers swap its reply text, status
or content for the case under test.
"""
from __future__ import annotations

import json
from pathlib import Path

RESPONSES = Path(__file__).parent / "fixtures" / "judge" / "responses"
JUDGE_DIR = RESPONSES / "gpt-6-luna" / "medium"


def _recorded() -> dict:
    """The recorded behaviour-verdict reply: one reasoning item and one message."""
    for path in sorted(JUDGE_DIR.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))["response"]
        if '"correct"' in body["output"][-1]["content"][0].get("text", ""):
            return body
    raise FileNotFoundError(f"no recorded behaviour verdict in {JUDGE_DIR}")


RECORDED = _recorded()


def _message(body: dict) -> dict:
    (message,) = [item for item in body["output"] if item["type"] == "message"]
    return message


def with_text(body: dict, text: str) -> dict:
    """*body* with its message holding one output text, *text*; other items are kept."""
    message = {**_message(body), "content": [
        {"type": "output_text", "text": text, "annotations": [], "logprobs": []}]}
    return {**body, "output": [i if i["type"] != "message" else message for i in body["output"]]}


def with_refusal(body: dict, refusal: str = "I can't help with that.") -> dict:
    """*body* with its message holding a refusal instead of text."""
    message = {**_message(body), "content": [{"type": "refusal", "refusal": refusal}]}
    return {**body, "output": [i if i["type"] != "message" else message for i in body["output"]]}


def incomplete(body: dict, reason: str = "max_output_tokens") -> dict:
    """*body* stopped early, as when reasoning uses up ``max_output_tokens``."""
    return {**body, "status": "incomplete", "incomplete_details": {"reason": reason},
            "output": [i for i in body["output"] if i["type"] != "message"]}
