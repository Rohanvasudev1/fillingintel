"""Step 13's spend, summed over committed files (Step 13).

Step 13 is capped at STEP_13_CAP_USD in all (user decision, 2026-10-16).
Counted:

- every route run (``benchmarks/router/route-run{N}.json``): its ``spent_usd``,
  the classifier calls not replayed from the response cache;
- every router run and every ``test`` run under ``benchmarks/runs``: per
  question, each call not replayed (query embedding, routing, graph stages,
  answer), and its judges when its answer was fresh, since a replayed answer's
  judge requests are replayed too; a retrieval-only run adds its fresh
  embeddings only;
- the recorded router test replies (``tests/fixtures/anthropic/route_*.json``),
  in full;
- calibration (ticket 04): each line of ``benchmarks/calibration/claims.jsonl``
  (its ``spent_usd``, the claims call when it was not replayed) and every
  ``corruption-run{N}.json`` (its ``spent_usd``).

A local file not yet committed counts too, which errs high.  Tickets 01 and 02
made no model call.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

from eval.calibration.scores import CALIBRATION_DIR, CLAIMS_FILE
from ingest.provenance import REPO_ROOT
from retrieve.answer_model import parse_reply
from retrieve.pricing import anthropic_cost
from retrieve.route_classifier import ROUTE_MODEL, ROUTE_PRICES

STEP_13_CAP_USD = 10.0
ROUTER_DIR = REPO_ROOT / "benchmarks" / "router"
RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "anthropic"
CORRUPTION_RUNS = "corruption-run*.json"
ROUTE_RUNS = "route-run*.json"
ROUTE_FIXTURES = "route_*.json"


def spent_usd(router_dir: Path = ROUTER_DIR, runs_dir: Path = RUNS_DIR,
              fixtures_dir: Path = FIXTURES_DIR,
              calibration_dir: Path = CALIBRATION_DIR) -> float:
    """What Step 13's committed files cost; ValueError naming a file it cannot read."""
    total = sum(_read(p, _spent) for p in sorted(router_dir.glob(ROUTE_RUNS)))
    total += sum(_read(p, _run_usd) for p in sorted(runs_dir.glob("*.json")))
    total += sum(_read(p, _fixture_usd) for p in sorted(fixtures_dir.glob(ROUTE_FIXTURES)))
    total += sum(_read(p, _spent) for p in sorted(calibration_dir.glob(CORRUPTION_RUNS)))
    claims = calibration_dir / CLAIMS_FILE
    return total + (_claims_usd(claims) if claims.exists() else 0.0)


def _spent(document: Mapping) -> float:
    return float(document["spent_usd"])


def _claims_usd(path: Path) -> float:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return sum(float(json.loads(line)["spent_usd"]) for line in lines if line.strip())
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read {path.name}: {type(exc).__name__}: {exc}") from exc


def _read(path: Path, cost: Callable[[Mapping], float]) -> float:
    try:
        return cost(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read {path.name}: {type(exc).__name__}: {exc}") from exc


def _run_usd(document: Mapping) -> float:
    header = document["header"]
    if header["arm"] != "router" and header["split"] != "test":
        return 0.0
    return sum(_question_usd(q) for q in document["questions"])


def _question_usd(question: Mapping) -> float:
    cost = question["cost_usd"] if "cost_usd" in question else {}
    usd = 0.0 if question["query_cached"] else float(cost.get("embedding", 0.0))
    routing = question.get("routing")
    if routing is not None and not routing["cost"]["from_cache"]:
        usd += float(routing["cost"]["cost_usd"])
    stages = (question.get("graph") or {}).get("stages", {})
    usd += sum(float(s["cost_usd"]) for s in stages.values() if not s["from_cache"])
    answer = question.get("answer")
    if answer is not None and not answer["from_cache"]:
        usd += float(cost["generation"]) + float((question.get("judged") or {})
                                                 .get("cost_usd") or 0.0)
    return usd


def _fixture_usd(document: Mapping) -> float:
    return sum(anthropic_cost(ROUTE_MODEL, parse_reply(call["response"]).usage, ROUTE_PRICES)
               for call in document["calls"])
