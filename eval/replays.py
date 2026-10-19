"""Which questions a run would replay from the response cache (Step 13, ticket 05).

A pre-run estimate credits work the response cache already holds (user decision,
2026-10-18), so the Step 13 cap check does not refuse a router run whose answers
the arms' own ``test`` runs already wrote.  The cache is keyed by request, which
an estimate cannot build without retrieval, so the committed run files stand in
for it:

- an arm's answer to a question (with its query embedding, graph calls and
  judges) replays when a full run of that arm under the frozen config, judged by
  the pinned judge (model, effort, max output tokens and prompts as in code) on the
  same response cache, answered and judged it;
- the router's classification of a question replays when a router run on the
  same cache classified it with the frozen router's model, effort and prompt.

A router run's inner answers are not credited to the arms.  A run that stopped
partway wrote no file, so its cached work is not credited, which errs high.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from eval.freeze import provenance_sha256
from eval.judging.openai_judge import JUDGE_EFFORT, JUDGE_MAX_OUTPUT_TOKENS, JUDGE_MODEL
from eval.router_report import PROVENANCE_KEYS
from retrieve.router import ROUTER_NAME

ROUTER = ROUTER_NAME
FULL = "full"
JUDGE = "judge"  # the judge's entry in a run header's models and efforts


@dataclass(frozen=True)
class Replays:
    answered: Mapping[str, frozenset[str]] = field(default_factory=dict)  # arm -> question IDs
    routed: frozenset[str] = frozenset()

    def answered_by(self, question_id: str) -> frozenset[str]:
        return frozenset(arm for arm, ids in self.answered.items() if question_id in ids)


NO_REPLAYS = Replays()


def known_replays(runs_dir: Path, frozen: Mapping[str, object], cache_name: str) -> Replays:
    """What runs under *runs_dir* on the cache folder *cache_name* replay, against the
    *frozen* record; ValueError naming a file it cannot read."""
    answered: dict[str, set[str]] = defaultdict(set)
    routed: set[str] = set()
    for path in sorted(runs_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            header = document["header"]
            if not _same_cache(header, cache_name):
                continue
            if header["arm"] == ROUTER:
                if header["router"]["model"] == frozen["router"]["model"]:
                    routed |= _routed(document["questions"], frozen["router"])
            elif (header.get("mode", FULL) == FULL and _pinned_judge(header["judging"])
                  and _frozen_arm(header, frozen)):
                answered[header["arm"]] |= {q["id"] for q in document["questions"]
                                            if q.get("answer") and q.get("judged")}
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"cannot read {path.name}: {type(exc).__name__}: {exc}") from exc
    return Replays({arm: frozenset(ids) for arm, ids in answered.items() if ids},
                   frozenset(routed))


def _same_cache(header: Mapping, cache_name: str) -> bool:
    cache = header["response_cache"]
    return not cache["uncached"] and cache["dir"] == cache_name


def _pinned_judge(judging: Mapping) -> bool:
    return (judging["model"], judging["effort"], judging["max_output_tokens"]) == (
        JUDGE_MODEL, JUDGE_EFFORT, JUDGE_MAX_OUTPUT_TOKENS)


def _frozen_arm(header: Mapping, frozen: Mapping[str, object]) -> bool:
    """The header's arm provenance, its judge entries set aside, matches the frozen hash."""
    pinned = frozen["arms"].get(header["arm"])
    provenance = {key: header.get(key) for key in PROVENANCE_KEYS}
    for key in ("models", "efforts"):
        provenance[key] = {k: v for k, v in provenance[key].items() if k != JUDGE}
    return isinstance(pinned, dict) and pinned.get("config_sha256") == provenance_sha256(
        provenance)


def _routed(questions: list[Mapping], router: Mapping) -> set[str]:
    return {q["id"] for q in questions
            if (r := q.get("routing")) is not None
            and (r["effort"], r["prompt_version"], r["prompt_sha256"])
            == (router["effort"], router["prompt_version"], router["prompt_sha256"])}
