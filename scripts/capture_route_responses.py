"""Record real router classifier replies as tests/fixtures/anthropic/route_{name}.json.

Two fixtures, both under the frozen classifier settings (``ROUTE_PROMPT_VERSION``
at ``ROUTE_EFFORT``):

- ``route_invented``: the invented questions in ``tests/router_fakes.QUESTIONS``,
  one per predicted class, for the router tests;
- ``route_dev``: the six dev records ``tests/test_eval_run`` uses, for the
  ``eval.route`` tests.  ``eval.route`` already classified them, so they replay
  from the response cache at no cost.

Each fixture holds the prompt version, effort and, per call, the question, the
request's SHA-256 and the response body exactly as the API returned it. No
credentials are written. Requests go through the response cache. Prints its
estimate and sends nothing without --send. Needs ``ANTHROPIC_API_KEY`` for
uncached requests; ``eval.step13_spend`` counts these fixtures in Step 13's
spend:

    env -u ANTHROPIC_BASE_URL uv run --env-file .env \
        python scripts/capture_route_responses.py --send
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

from eval.schema import load_records  # noqa: E402
from retrieve.answer_model import AnthropicAnswerModel  # noqa: E402
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel  # noqa: E402
from retrieve.route_classifier import (  # noqa: E402
    ROUTE_EFFORT,
    ROUTE_PROMPT_VERSION,
    build_route_request,
    estimate_request_usd,
    read_route_reply,
)
from retrieve.route_prompt import load_route_prompt  # noqa: E402
from tests.router_fakes import DEV_IDS, FIXTURES, QUESTIONS  # noqa: E402

NOTE = "real API response, recorded by scripts/capture_route_responses.py"


def _questions() -> dict[str, list[tuple[str | None, str]]]:
    records = {r.id: r for r in load_records(REPO / "eval" / "agent_drafted_set.jsonl")}
    return {"invented": [(None, q) for q in QUESTIONS.values()],
            "dev": [(i, records[i].question) for i in DEV_IDS]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--send", action="store_true", help="send the uncached requests")
    args = parser.parse_args()
    prompt = load_route_prompt(ROUTE_PROMPT_VERSION)
    groups = _questions()
    cache = CachedAnswerModel(None, DEFAULT_CACHE_DIR)
    requests = {name: [(i, q, build_route_request(prompt, q, ROUTE_EFFORT)) for i, q in group]
                for name, group in groups.items()}
    uncached = [r for group in requests.values() for _, _, r in group if not cache.contains(r)]
    print(f"{prompt.prompt_version} at {ROUTE_EFFORT}: {len(uncached)} uncached requests, "
          f"estimate ${sum(estimate_request_usd(r) for r in uncached):.4f}")
    if not args.send:
        print("nothing sent; rerun with --send")
        return 0
    with AnthropicAnswerModel.from_env() as claude:
        model = CachedAnswerModel(claude, DEFAULT_CACHE_DIR)
        for name, group in requests.items():
            calls = []
            for record_id, question, request in group:
                response = model.complete(request)
                reply = read_route_reply(response.body)
                print(f"{name}: {reply.predicted_class} <- {question}")
                calls.append({"id": record_id, "question": question,
                              "request_sha256": request.cache_key(), "api_ms": response.api_ms,
                              "response": dict(response.body)})
            path = FIXTURES / f"route_{name}.json"
            path.write_text(json.dumps({"note": NOTE, "prompt_version": prompt.prompt_version,
                                        "effort": ROUTE_EFFORT, "calls": calls},
                                       indent=2) + "\n", encoding="utf-8")
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
