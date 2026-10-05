"""Record real judge responses in tests/fixtures/judge/, and check the judge live.

Runs judge run 1 on the three recorded answers in tests/fixtures/anthropic/:
q0072 (lookup: faithfulness, answer relevancy, citation support), q0005
(decline correctness) and q0011 (not-found correctness).  The judge's response
cache and Voyage query cache point into the fixture directory, so the files
written there are the API responses exactly as returned, and a test replays
them offline.  The chunk texts the answer model saw are read from
``inputs.json``; only when that file is missing are they read from Postgres
and written there.  No credentials are written; the script checks the recorded
files for key and organisation ID patterns and fails if it finds one.

It is also the live check the research note asks for: ``gpt-6-luna`` accepts
``reasoning.effort`` together with a strict ``json_schema`` text format through
openai 3.3.0, and its replies parse as the Ragas and project schemas.  It prints
the reasoning tokens luna used.

Needs ``OPENAI_API_KEY``, ``VOYAGE_API_KEY`` and the network (plus
``DATABASE_URL`` when ``inputs.json`` is missing); costs about eight luna calls
and a few Voyage embeddings, well under one cent.

    uv run --env-file .env python scripts/capture_judge_responses.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.judging.openai_backend import OpenAIResponsesModel  # noqa: E402
from eval.judging.scoring import JudgeInput, RagasJudges  # noqa: E402
from eval.schema import load_records  # noqa: E402
from ingest.corpus import TICKER_BY_CIK  # noqa: E402
from ingest.store import get_chunk, resolve  # noqa: E402
from ingest.voyage import VoyageClient, VoyageError  # noqa: E402
from retrieve.answer import write_answer  # noqa: E402
from retrieve.answer_model import AnswerModelError, ApiResponse, ModelRequest  # noqa: E402
from retrieve.answer_prompt import SourceChunk, load_prompt  # noqa: E402
from retrieve.query_cache import CachedQueryEmbedder  # noqa: E402
from retrieve.response_cache import CachedAnswerModel  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ANSWERS = REPO / "tests" / "fixtures" / "anthropic"
OUT = REPO / "tests" / "fixtures" / "judge"
CASES = ("answered_q0072", "declined_q0005", "not_found_q0011")
NOTE = "chunk texts from Postgres, recorded by scripts/capture_judge_responses.py"
# OpenAI and Anthropic secret keys, and OpenAI organisation and project IDs.
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}|\borg-[A-Za-z0-9]{8,}|\bproj_[A-Za-z0-9]{8,}")


class _Replay:
    def __init__(self, body: dict[str, object]):
        self._response = ApiResponse(body, 0.0)

    def complete(self, request: ModelRequest) -> ApiResponse:
        return self._response


def _sources(conn: psycopg.Connection, chunk_ids: list[str]) -> list[dict]:
    rows = []
    for chunk_id in chunk_ids:
        chunk = get_chunk(conn, chunk_id)
        rows.append({"chunk_id": chunk_id, "company": TICKER_BY_CIK[chunk.cik],
                     "form_type": chunk.form_type, "fiscal_period": chunk.fiscal_period,
                     "section": chunk.section, "text": resolve(conn, chunk_id)})
    return rows


def _judge_case(judges: RagasJudges, name: str, sources: list[dict], classes: dict) -> None:
    fixture = json.loads((ANSWERS / f"{name}.json").read_text(encoding="utf-8"))
    chunks = tuple(SourceChunk(**s) for s in sources)
    answer = write_answer(fixture["question"], chunks, _Replay(fixture["response"]),
                          load_prompt("v1"))
    item = JudgeInput(fixture["question"], classes[fixture["question_id"]], answer, chunks)
    scores = judges.judge(item, run=1)
    print(f"{name}: {dict(scores.scores)} errors {dict(scores.errors)} "
          f"calls {scores.calls} cost ${scores.cost_usd:.4f} "
          f"output tokens {scores.usage.output_tokens} "
          f"(reasoning {scores.usage.reasoning_tokens})")


def _inputs_from_postgres() -> dict[str, list[dict]] | None:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set (run with: uv run --env-file .env ...)", file=sys.stderr)
        return None
    try:
        conn = psycopg.connect(os.environ["DATABASE_URL"])
    except psycopg.Error as exc:  # the message can quote the URL
        print(f"cannot connect to the database: {type(exc).__name__}", file=sys.stderr)
        return None
    with conn:
        inputs = {
            name: _sources(conn, json.loads((ANSWERS / f"{name}.json").read_text(encoding="utf-8"))
                           ["retrieved_chunk_ids"])
            for name in CASES
        }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "inputs.json").write_text(
        json.dumps({"recorded": NOTE, "sources": inputs}, indent=2) + "\n", encoding="utf-8")
    return inputs


def _secrets_in(directory: Path) -> list[str]:
    """Files under *directory* whose text matches a key or organisation ID pattern."""
    return [p.relative_to(REPO).as_posix() for p in sorted(directory.rglob("*.json"))
            if SECRET_PATTERN.search(p.read_text(encoding="utf-8"))]


def main() -> int:
    classes = {r.id: r.class_ for r in load_records(REPO / "eval" / "agent_drafted_set.jsonl")}
    inputs_file = OUT / "inputs.json"
    if inputs_file.exists():
        inputs = json.loads(inputs_file.read_text(encoding="utf-8"))["sources"]
    else:
        inputs = _inputs_from_postgres()
        if inputs is None:
            return 2
    try:
        with OpenAIResponsesModel.from_env() as backend, VoyageClient.from_env() as voyage:
            judges = RagasJudges(CachedAnswerModel(backend, OUT / "responses"),
                                 CachedQueryEmbedder(voyage, OUT / "embeddings"))
            for name in CASES:
                _judge_case(judges, name, inputs[name], classes)
    except (AnswerModelError, VoyageError) as exc:
        print(f"judge call failed: {exc}", file=sys.stderr)
        return 1
    leaks = _secrets_in(OUT)
    if leaks:
        print(f"recorded files hold a key or organisation ID: {leaks}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
