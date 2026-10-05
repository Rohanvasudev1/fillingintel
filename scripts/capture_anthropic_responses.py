"""Record real answer-model responses as tests/fixtures/anthropic/{name}.json.

Runs the vector arm, uncached, on three dev questions: one answered (q0072), one
investment-advice decline (q0005) and one the filings cannot answer (q0011).
Each fixture holds the question, the retrieved chunk IDs and the response body
exactly as the API returned it.  No credentials are written.

Needs ``DATABASE_URL``, ``VOYAGE_API_KEY``, ``ANTHROPIC_API_KEY`` and the
network; costs about three answer calls (a few US cents).

    uv run --env-file .env python scripts/capture_anthropic_responses.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.question_sets import EVAL_DIR  # noqa: E402
from eval.schema import load_records  # noqa: E402
from ingest.voyage import VoyageClient, VoyageError  # noqa: E402
from retrieve.answer_model import (  # noqa: E402
    AnswerModelError,
    AnswerRequest,
    AnthropicAnswerModel,
    ApiResponse,
)
from retrieve.arm import ArmError  # noqa: E402
from retrieve.query_cache import CachedQueryEmbedder  # noqa: E402
from retrieve.vector import VectorArm  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "anthropic"
CASES = {"answered_q0072": "q0072", "declined_q0005": "q0005", "not_found_q0011": "q0011"}
NOTE = "real API response, recorded by scripts/capture_anthropic_responses.py"


class _Recorder:
    """Passes each request to the real model and keeps the last response."""

    def __init__(self, inner: AnthropicAnswerModel):
        self._inner = inner
        self.last: ApiResponse | None = None

    def complete(self, request: AnswerRequest) -> ApiResponse:
        self.last = self._inner.complete(request)
        return self.last


def _capture(arm: VectorArm, recorder: _Recorder) -> int:
    records = {r.id: r for r in load_records(EVAL_DIR / "agent_drafted_set.jsonl")}
    for name, record_id in CASES.items():
        question = records[record_id].question
        try:
            result = arm.run(question)
        except ArmError as exc:
            print(f"{record_id} failed: {exc}", file=sys.stderr)
            return 1
        if recorder.last is None or not recorder.last.body:
            print(f"{record_id}: no response was recorded", file=sys.stderr)
            return 1
        fixture = {
            "recorded": NOTE,
            "question_id": record_id,
            "question": question,
            "retrieved_chunk_ids": [r.chunk_id for r in result.retrieved],
            "api_ms": recorder.last.api_ms,
            "response": dict(recorder.last.body),
        }
        try:
            (OUT / f"{name}.json").write_text(json.dumps(fixture, indent=2) + "\n",
                                              encoding="utf-8")
        except OSError as exc:
            print(f"cannot write {name}.json: {exc}", file=sys.stderr)
            return 1
        check = result.answer.citation_check
        print(f"{name}: status {result.answer.status}, {check.sentences} sentences, "
              f"{len(check.dropped)} dropped")
    return 0


def main() -> int:
    try:
        conn = psycopg.connect(os.environ["DATABASE_URL"])
    except (KeyError, psycopg.Error) as exc:  # the message can quote the URL
        print(f"cannot connect to the database: {type(exc).__name__}", file=sys.stderr)
        return 2
    try:
        with conn, VoyageClient.from_env() as voyage, AnthropicAnswerModel.from_env() as claude:
            recorder = _Recorder(claude)
            return _capture(VectorArm(conn, CachedQueryEmbedder(voyage), recorder), recorder)
    except (VoyageError, AnswerModelError, ArmError) as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
