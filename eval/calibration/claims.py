"""``python -m eval.calibration.claims``: split each drawn answer into atomic claims
(Step 13 ticket 04).

One claude-sonnet-5-5 call per drawn answer of an answerable question, at effort
``high``, with structured output ``{claims: [{sentence_index, text}]}`` under
``prompts/claims/v1.md``.  The call is separate from the judge's: the user checks
these claims, not the judge's own statements.  It sees the question and the kept
sentences, numbered from 1 without their citations, never the chunks or a judge
score.  A reply cut off, refused, off the schema, with no claims or naming a
sentence the answer lacks stops the command.

Calls go through the response cache.  The estimate prices only requests the
cache lacks and is printed first; the command is refused above ``--max-cost``
(default $1) or past Step 13's cap with the committed spend.  It writes
``benchmarks/calibration/claims.jsonl`` once, one line per answer in draw order.

    env -u ANTHROPIC_BASE_URL uv run --env-file .env python -m eval.calibration.claims

Exit codes: 0 written; 2 refused, bad arguments or the file exists; 3 could not run.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from eval.calibration.answers import RUNS_DIR, AnswerFileError, RunAnswer, load_answers
from eval.calibration.draw import CALIBRATION_DIR, DRAW_PATH, DrawError, drawn_answers, read_draw
from eval.calibration.scores import CLAIMS_FILE
from eval.step13_spend import STEP_13_CAP_USD, spent_usd
from ingest.chunker import count_tokens
from ingest.provenance import REPO_ROOT, git_state
from resolve.cli import cost_cap
from retrieve.answer_model import (
    API_KEY_ENV,
    AnswerModel,
    AnswerModelError,
    AnthropicAnswerModel,
    ApiResponse,
    Effort,
    ModelRequest,
    parse_reply,
)
from retrieve.citations import strip_citations
from retrieve.pricing import TABLE_2026_10_10, anthropic_cost
from retrieve.query_cache import CacheError
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel

KIND = "claims"
CLAIMS_PROMPTS_DIR = REPO_ROOT / "prompts" / KIND
PUBLISHED: Mapping[str, str] = {
    "v1": "a2c8121ce9b2516ac4492a9f4acc149351e16c3445a42d15d38243a321da351a",
}
CLAIMS_VERSION = "v1"
CLAIMS_MODEL = "claude-sonnet-5-5"
CLAIMS_EFFORT: Effort = "high"
CLAIMS_MAX_TOKENS = 16_000
CLAIMS_PRICES = TABLE_2026_10_10
TOKEN_MARGIN = 2.0  # cl100k_base counts times this, as for the router's estimate
ASSUMED_OUTPUT_TOKENS = 1_500  # thinking plus a claims list
DEFAULT_MAX_COST = 1.0
BASE_URL_ENV = "ANTHROPIC_BASE_URL"
EXIT_OK, EXIT_USAGE, EXIT_COULD_NOT_RUN = 0, 2, 3
HASH_CHARS = 8
_PARTS = re.compile(r"\A# System\n(?P<system>.*?)\n# User\n(?P<user>.*)\Z", re.DOTALL)
_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claims"],
    "properties": {"claims": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["sentence_index", "text"],
        "properties": {
            "sentence_index": {"type": "integer",
                               "description": "the number of the sentence the claim is from"},
            "text": {"type": "string", "description": "one atomic claim, readable alone"},
        },
    }}},
}
_STOPPED = {"max_tokens": "cut off at max_tokens", "refusal": "refused"}


class ClaimsPromptError(RuntimeError):
    """A claims prompt version is unknown, changed after publication, or malformed."""


class ClaimsError(RuntimeError):
    """A claims reply cannot be used; the message is safe to print."""


@dataclass(frozen=True)
class ClaimsPrompt:
    version: str
    sha256: str  # of the system prompt, the user template and the output schema
    path: str
    system: str
    user_template: str

    @property
    def prompt_version(self) -> str:
        return f"{KIND}/{self.version}@{self.sha256[:HASH_CHARS]}"


def load_claims_prompt(version: str = CLAIMS_VERSION,
                       prompts_dir: Path = CLAIMS_PROMPTS_DIR) -> ClaimsPrompt:
    """The published claims prompt *version*; refuses a file that no longer matches."""
    if version not in PUBLISHED:
        raise ClaimsPromptError(f"claims prompt {version} is not published")
    path = prompts_dir / f"{version}.md"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ClaimsPromptError(f"cannot read claims prompt {version}: "
                                f"{type(exc).__name__}") from exc
    if hashlib.sha256(raw).hexdigest() != PUBLISHED[version]:
        raise ClaimsPromptError(f"claims {version} has changed since it was published; "
                                "write the change as a new version instead")
    parts = _PARTS.match(raw.decode("utf-8"))
    if parts is None:
        raise ClaimsPromptError(f"claims prompt {version} lacks '# System' and '# User'")
    system, user = parts["system"].strip(), parts["user"].strip()
    sent = json.dumps({"system": system, "user": user, "schema": _SCHEMA}, sort_keys=True,
                      ensure_ascii=False)
    return ClaimsPrompt(version, hashlib.sha256(sent.encode("utf-8")).hexdigest(),
                        f"prompts/{KIND}/{version}.md", system, user)


@dataclass(frozen=True)
class ClaimsRequest:
    system: str
    user: str
    model: str = CLAIMS_MODEL
    effort: Effort = CLAIMS_EFFORT
    max_tokens: int = CLAIMS_MAX_TOKENS

    def params(self) -> dict[str, object]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": self.system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": self.user}],
            "output_config": {"effort": self.effort,
                              "format": {"type": "json_schema",
                                         "schema": copy.deepcopy(_SCHEMA)}},
        }

    def cache_key(self) -> str:
        canonical = json.dumps(self.params(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Claim:
    sentence_index: int  # 1-based, as the sentences were numbered
    text: str


@dataclass(frozen=True)
class ClaimsReply:
    claims: tuple[Claim, ...]
    cost_usd: float


def build_claims_request(prompt: ClaimsPrompt, answer: RunAnswer) -> ClaimsRequest:
    sentences = "\n".join(f"{i}. {strip_citations(s.text)}"
                          for i, s in enumerate(answer.kept, start=1))
    user = Template(prompt.user_template).substitute(question=answer.question,
                                                     sentences=sentences)
    return ClaimsRequest(system=prompt.system, user=user)


def read_claims_reply(body: Mapping[str, Any], sentences: int) -> ClaimsReply:
    """The claims in *body*; ``ClaimsError`` when the reply cannot be used."""
    reply = parse_reply(body)
    stopped = _STOPPED.get(reply.stop_reason or "")
    if stopped is not None:
        raise ClaimsError(f"the claims reply was {stopped}")
    try:
        items = json.loads(reply.text)["claims"]
        claims = tuple(Claim(int(c["sentence_index"]), str(c["text"]).strip()) for c in items)
    except (ValueError, KeyError, TypeError) as exc:
        raise ClaimsError("the claims reply is off the schema") from exc
    if not claims:
        raise ClaimsError("the claims reply holds no claims")
    for claim in claims:
        if not 1 <= claim.sentence_index <= sentences:
            raise ClaimsError(f"a claim names sentence {claim.sentence_index}; the answer has "
                              f"{sentences}")
        if not claim.text:
            raise ClaimsError("a claim is empty")
    return ClaimsReply(claims, anthropic_cost(CLAIMS_MODEL, reply.usage, CLAIMS_PRICES))


def estimate_request_usd(request: ClaimsRequest) -> float:
    price = CLAIMS_PRICES.price(CLAIMS_MODEL)
    tokens = (count_tokens(request.system) + count_tokens(request.user)) * TOKEN_MARGIN
    return (tokens * price.input_per_mtok
            + ASSUMED_OUTPUT_TOKENS * price.output_per_mtok) / 1_000_000


def read_claims(path: Path) -> dict[str, tuple[Claim, ...]]:
    """Answer key -> its claims, from the claims file; ``ClaimsError`` if unreadable."""
    try:
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        return {line["key"]: tuple(Claim(int(c["sentence_index"]), str(c["text"]))
                                   for c in line["claims"]) for line in lines}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ClaimsError(f"cannot read {path.name}: {type(exc).__name__}") from exc


class _NoCalls:
    def complete(self, request: ModelRequest) -> ApiResponse:
        raise AnswerModelError("a cached request was not in the response cache")


def _live_model(stack: ExitStack) -> AnswerModel:
    if os.environ.get(BASE_URL_ENV):
        raise AnswerModelError(f"{BASE_URL_ENV} is set; unset it so the key goes only to "
                               "Anthropic (env -u ANTHROPIC_BASE_URL ...)")
    if not os.environ.get(API_KEY_ENV):
        raise AnswerModelError(f"{API_KEY_ENV} is not set (uv run --env-file .env ...)")
    return stack.enter_context(AnthropicAnswerModel.from_env())


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split the drawn answers into claims.")
    parser.add_argument("--draw", type=Path, default=DRAW_PATH)
    parser.add_argument("--output", type=Path, default=CALIBRATION_DIR / CLAIMS_FILE)
    parser.add_argument("--max-cost", type=cost_cap, default=DEFAULT_MAX_COST, metavar="USD")
    return parser.parse_args(argv)


def _line(prompt: ClaimsPrompt, answer: RunAnswer, reply: ClaimsReply,
          response: ApiResponse) -> dict[str, object]:
    return {
        "key": answer.key, "arm": answer.arm, "question_id": answer.question_id,
        "sentences": len(answer.kept),
        "claims": [{"sentence_index": c.sentence_index, "text": c.text} for c in reply.claims],
        "prompt": prompt.prompt_version, "prompt_sha256": prompt.sha256,
        "model": CLAIMS_MODEL, "effort": CLAIMS_EFFORT,
        "cost_usd": reply.cost_usd, "from_cache": response.from_cache,
        "spent_usd": 0.0 if response.from_cache else reply.cost_usd,
    }


def main(argv: Sequence[str] | None = None, *, runs_dir: Path = RUNS_DIR,
         response_cache: Path = DEFAULT_CACHE_DIR, model: AnswerModel | None = None,
         spent: Callable[[], float] = spent_usd) -> int:
    """*model* replaces the Anthropic client (tests); *spent* reads committed spend."""
    try:
        args = _parse(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    if args.output.exists():
        print(f"{args.output} exists; the claims are written once", file=sys.stderr)
        return EXIT_USAGE
    try:
        prompt = load_claims_prompt()
        answers = [a for a in drawn_answers(read_draw(args.draw), load_answers(runs_dir))
                   if a.kind == "answer"]
        committed = spent()
    except (ClaimsPromptError, DrawError, AnswerFileError, ValueError) as exc:
        print(f"could not start: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    requests = [build_claims_request(prompt, a) for a in answers]
    cache = CachedAnswerModel(_NoCalls(), response_cache)
    estimate = sum(estimate_request_usd(r) for r in requests if not cache.contains(r))
    print(f"estimate: ${estimate:.4f} for {len(requests)} answers under "
          f"{prompt.prompt_version} (cached requests cost nothing); Step 13 spent "
          f"${committed:.2f} of ${STEP_13_CAP_USD:.2f}")
    if estimate > args.max_cost or committed + estimate > STEP_13_CAP_USD:
        print(f"refused: the estimate ${estimate:.4f} is above --max-cost ${args.max_cost:.2f} "
              f"or would pass the Step 13 cap; nothing sent", file=sys.stderr)
        return EXIT_USAGE
    try:
        with ExitStack() as stack:
            inner = model or (_live_model(stack) if estimate > 0 else _NoCalls())
            cached = CachedAnswerModel(inner, response_cache)
            lines = []
            for answer, request in zip(answers, requests, strict=True):
                response = cached.complete(request)
                lines.append(_line(prompt, answer,
                                   read_claims_reply(response.body, len(answer.kept)),
                                   response))
    except (AnswerModelError, ClaimsError, CacheError, OSError) as exc:
        print(f"stopped, nothing written: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps({**line, "commit": git_state()},
                                              ensure_ascii=False, sort_keys=True) + "\n"
                                   for line in lines), encoding="utf-8")
    spent_now = sum(float(line["spent_usd"]) for line in lines)
    print(f"wrote {args.output}: {sum(len(line['claims']) for line in lines)} claims over "
          f"{len(lines)} answers; spent ${spent_now:.4f}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

