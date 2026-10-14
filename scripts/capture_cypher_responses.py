"""Record real Cypher generation replies as tests/fixtures/anthropic/cypher_{name}.json.

Sends the three invented questions in tests/traversal_data.py to the Cypher model
with the current prompt, rendered for the three fixture filings the
graph_traversal tests load, uncached. Each fixture holds the question, the
prompt version and the response body exactly as the API returned it. No
credentials are written.

Prints its estimate and sends nothing without --send. Needs ``ANTHROPIC_API_KEY``
and the network:

    env -u ANTHROPIC_BASE_URL uv run --env-file .env \
        python scripts/capture_cypher_responses.py --send
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

from retrieve.answer_model import AnswerModelError, AnthropicAnswerModel  # noqa: E402
from retrieve.cypher_estimate import estimate_usd  # noqa: E402
from retrieve.cypher_prompt import load_prompt  # noqa: E402
from retrieve.cypher_request import first_request  # noqa: E402
from retrieve.question_filter import parse_question_filter  # noqa: E402
from tests.traversal_data import FILINGS, FIXTURES, QUESTIONS  # noqa: E402

NOTE = "real API response, recorded by scripts/capture_cypher_responses.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record real Cypher generation replies.")
    parser.add_argument("--send", action="store_true", help="make the calls (default: estimate)")
    args = parser.parse_args(argv)
    prompt = load_prompt(filings=FILINGS)
    requests = {name: first_request(prompt, q, parse_question_filter(q, FILINGS))
                for name, q in QUESTIONS.items()}
    print(f"{len(requests)} calls, {prompt.prompt_version}: estimate "
          f"${estimate_usd(requests.values()):.2f} (no repairs)")
    if not args.send:
        return 0
    try:
        with AnthropicAnswerModel.from_env() as claude:
            for name, request in requests.items():
                response = claude.complete(request)
                body = dict(response.body)
                (FIXTURES / f"cypher_{name}.json").write_text(json.dumps(
                    {"recorded": NOTE, "prompt_version": prompt.prompt_version,
                     "question": QUESTIONS[name], "api_ms": response.api_ms, "response": body},
                    indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                text = "".join(c.get("text", "") for c in body.get("content", []))
                print(f"{name}: stop {body.get('stop_reason')}, usage {body.get('usage')}\n"
                      f"{text}\n")
    except (AnswerModelError, OSError) as exc:
        print(f"capture failed: {exc!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
