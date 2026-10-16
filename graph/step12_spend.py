"""Step 12's spend, summed over the committed run files (Step 12).

Step 12 is capped at STEP_12_CAP_USD in all (user decision, 2026-10-15):
reports, embeddings, map and reduce calls, and judges. The total is the sum of
`cost_this_run_usd` over every reports run file in benchmarks/communities, as
extract.spend sums extraction reports, plus every graph_global run file in
benchmarks/runs: its questions' `cost_usd.total` and its `judge_usage.cost_usd`
(ticket 07). A run file prices replies replayed from the response cache as if
sent, and a local file not yet committed counts too; both err high. Calls made
outside the commands, such as recording test replies, are not counted.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from graph.report_file import reports_runs
from ingest.provenance import REPO_ROOT

STEP_12_CAP_USD = 6.0
COMMUNITIES_DIR = REPO_ROOT / "benchmarks" / "communities"
RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"
GLOBAL_RUNS = "*-graph_global-*.json"


def spent_usd(communities_dir: Path = COMMUNITIES_DIR, runs_dir: Path = RUNS_DIR) -> float:
    """What Step 12's committed runs cost; ValueError naming a file it cannot read."""
    return _reports_usd(communities_dir) + sum(
        _global_run_usd(p) for p in sorted(runs_dir.glob(GLOBAL_RUNS)))


def _finite(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value)


def _global_run_usd(path: Path) -> float:
    """A graph_global run's question and judge costs; 0 for another arm's file."""
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
        if run["header"]["arm"] != "graph_global":
            return 0.0
        costs = [q["cost_usd"]["total"] for q in run["questions"]]
        costs.append(run["judge_usage"]["cost_usd"])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read {path.name}: {type(exc).__name__}: {exc}") from exc
    if not all(_finite(c) for c in costs):
        raise ValueError(f"{path.name} holds a cost that is not a number")
    return sum(costs)


def _reports_usd(communities_dir: Path) -> float:
    total = 0.0
    for path in reports_runs(communities_dir):
        try:
            with path.open(encoding="utf-8") as fh:
                header = json.loads(fh.readline())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read {path.name}: {type(exc).__name__}") from exc
        cost = header.get("cost_this_run_usd") if isinstance(header, dict) else None
        if not _finite(cost):
            raise ValueError(f"{path.name} has no cost_this_run_usd in its header")
        total += cost
    return total
