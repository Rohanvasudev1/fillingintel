"""Record real graph_global map and reduce replies as tests/fixtures/anthropic/global_{name}.json.

Sends the two dev global questions in tests/global_data.py through the map and
reduce steps exactly as the graph_global tests run them: on the test graph each
selects three communities, whose map calls are shown SHOWN_10K's chunks from
NVIDIA's FY2026 10-K with the test reports, uncached. The chunk texts come from
data/parsed, as Postgres holds them. Each fixture holds the question, both
prompt versions, and per call the community, the request's SHA-256 and the
response body exactly as the API returned it. No credentials are written. The
tests replay the replies by request hash, so a request built differently from
the arm's fails there.

Prints its estimate and sends nothing without --send. Needs ``ANTHROPIC_API_KEY``
and the network; its calls are not counted in Step 12's spend, so the BUILD-LOG
records them:

    env -u ANTHROPIC_BASE_URL uv run --env-file .env \
        python scripts/capture_global_responses.py --send
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(REPO / "vendor" / "tiktoken"))

from ingest.chunker import chunk_filing  # noqa: E402
from ingest.parsed_files import read_parsed  # noqa: E402
from retrieve.answer_model import AnswerModelError, AnthropicAnswerModel  # noqa: E402
from retrieve.answer_prompt import SourceChunk  # noqa: E402
from retrieve.arm import MapCall, StageCost  # noqa: E402
from retrieve.global_estimate import estimate_usd  # noqa: E402
from retrieve.global_map import build_map_request, read_map_reply  # noqa: E402
from retrieve.global_prompts import load_map_prompt, load_reduce_prompt  # noqa: E402
from retrieve.global_reduce import build_reduce_request, select_points  # noqa: E402
from tests.global_data import FIXTURES, QUESTIONS, SHOWN_10K, SUMMARIES, TITLES  # noqa: E402

NOTE = "real API response, recorded by scripts/capture_global_responses.py"
NVDA_10K = "0001045810-26-000021"
PARSED = REPO / "data" / "parsed" / f"{NVDA_10K}.json"


def _source_chunks() -> dict[str, SourceChunk]:
    record = read_parsed(PARSED)
    filing = record.filing
    return {c.chunk_id: SourceChunk(c.chunk_id, "NVDA", record.meta.form_type,
                                    record.meta.fiscal_period, c.section,
                                    filing.text[c.char_start:c.char_end])
            for c in chunk_filing(filing)}


def _map_requests(question: str, chunks: dict[str, SourceChunk]):
    prompt = load_map_prompt()
    return {community: build_map_request(prompt, question, TITLES[community],
                                         SUMMARIES[community], [chunks[c] for c in shown])
            for community, shown in SHOWN_10K.items()}


def _record(claude, name: str, question: str, chunks: dict[str, SourceChunk]) -> dict:
    reduce_prompt = load_reduce_prompt()
    map_calls, calls = [], []
    for rank, (community, request) in enumerate(_map_requests(question, chunks).items(), 1):
        response = claude.complete(request)
        reply = read_map_reply(response.body, SHOWN_10K[community], community)
        calls.append(MapCall(community, rank, 0.0, SHOWN_10K[community], reply.outcome,
                             reply.points, StageCost(ms=response.api_ms)))
        map_calls.append({"community_id": community, "request_sha256": request.cache_key(),
                          "api_ms": response.api_ms, "response": dict(response.body)})
        print(f"{name} {community}: {reply.outcome}, usage {response.body.get('usage')}\n"
              f"{reply.text}\n")
    selected = select_points(calls)
    if not selected.points:
        raise AnswerModelError(f"{name}: no map point scored above 0, so no reduce reply")
    request = build_reduce_request(reduce_prompt, question, selected.points)
    response = claude.complete(request)
    print(f"{name} reduce: usage {response.body.get('usage')}")
    return {"recorded": NOTE, "question": question,
            "map_prompt": load_map_prompt().prompt_version,
            "reduce_prompt": reduce_prompt.prompt_version, "map_calls": map_calls,
            "reduce": {"request_sha256": request.cache_key(), "api_ms": response.api_ms,
                       "response": dict(response.body)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record real graph_global replies.")
    parser.add_argument("--send", action="store_true", help="make the calls (default: estimate)")
    args = parser.parse_args(argv)
    if os.environ.get("ANTHROPIC_BASE_URL"):
        print("ANTHROPIC_BASE_URL is set; unset it so filing text and the API key go only to "
              "Anthropic", file=sys.stderr)
        return 1
    chunks = _source_chunks()
    requests = [r for q in QUESTIONS.values() for r in _map_requests(q, chunks).values()]
    estimate = estimate_usd(requests, load_reduce_prompt(), reduce_calls=len(QUESTIONS))
    print(f"{len(requests)} map calls and {len(QUESTIONS)} reduce calls: estimate "
          f"${estimate:.2f}")
    if not args.send:
        print("Estimate only; rerun with --send to record the replies.")
        return 0
    try:
        with AnthropicAnswerModel.from_env() as claude:
            for name, question in QUESTIONS.items():
                fixture = _record(claude, name, question, chunks)
                (FIXTURES / f"global_{name}.json").write_text(
                    json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (AnswerModelError, OSError) as exc:
        print(f"capture failed: {exc!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
