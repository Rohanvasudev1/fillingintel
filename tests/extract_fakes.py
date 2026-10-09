"""A fake extraction model and helpers for extract_filing() tests (Step 8).

Replies are keyed by chunk ID. A reply is a dict (sent as the JSON text of a
recorded Messages API body), a str (sent as raw text, for broken JSON) or an
exception (raised, as the real client raises on an API error).
"""
from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from extract.filers import FilingInfo
from extract.request import ChunkInput
from retrieve.answer_model import ApiResponse, ModelRequest
from tests.anthropic_fixtures import load, with_text
from tests.graph_test_data import NVDA_10K

NVDA_FILING = FilingInfo(cik="1045810", accession_no=NVDA_10K, company_name="NVIDIA CORP",
                         form_type="10-K", fiscal_period="FY2026")


def body(reply: object, stop_reason: str = "end_turn") -> dict:
    """A Messages API body whose text is *reply* (JSON-encoded unless it is a str)."""
    text = reply if isinstance(reply, str) else json.dumps(reply)
    return {**with_text(load("answered_q0072")["response"], text), "stop_reason": stop_reason}


class FakeExtractModel:
    """Answers each request with the reply for its chunk; records the chunk IDs asked."""

    def __init__(self, replies: Mapping[str, object]):
        self._replies = dict(replies)
        self.asked: list[str] = []

    def complete(self, request: ModelRequest) -> ApiResponse:
        chunk_id = request.chunk_id
        self.asked.append(chunk_id)
        reply = self._replies[chunk_id]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict) and "content" in reply:  # already a body
            return ApiResponse(body=reply, api_ms=1.0)
        return ApiResponse(body=body(reply), api_ms=1.0)


@pytest.fixture(scope="session")
def nvda_chunks(test_graph_chunks) -> dict[str, ChunkInput]:
    """chunk_id -> ChunkInput for every chunk of the NVDA FY2026 10-K fixture."""
    return {
        chunk_id: ChunkInput(chunk_id, section, text)
        for chunk_id, (section, text) in test_graph_chunks.items()
        if chunk_id.startswith(NVDA_10K)
    }


def node(local_id: str, label: str, name: object, span: str, confidence: str = "stated",
         **metric: object) -> dict:
    return {"id": local_id, "label": label, "name": name,
            "value": metric.get("value"), "unit": metric.get("unit"),
            "period": metric.get("period"), "evidence_span": span, "confidence": confidence}


def triple(start: str, edge_type: str, end: str, span: str, confidence: str = "stated",
           role: object = None, stake: object = None) -> dict:
    return {"start": start, "type": edge_type, "end": end, "role": role, "stake": stake,
            "evidence_span": span, "confidence": confidence}
