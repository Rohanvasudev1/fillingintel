"""Step 12's spend, summed over the committed run files (Step 12).

Step 12 is capped at STEP_12_CAP_USD in all (user decision, 2026-10-15):
reports, embeddings, map and reduce calls, and judges. The total is the sum of
`cost_this_run_usd` over every reports run file in benchmarks/communities, as
extract.spend sums extraction reports; later tickets add their own run files
here. A local file not yet committed counts too, which errs high. Calls made
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


def spent_usd(communities_dir: Path = COMMUNITIES_DIR) -> float:
    """What Step 12's committed runs cost; ValueError naming a file it cannot read."""
    total = 0.0
    for path in reports_runs(communities_dir):
        try:
            with path.open(encoding="utf-8") as fh:
                header = json.loads(fh.readline())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read {path.name}: {type(exc).__name__}") from exc
        cost = header.get("cost_this_run_usd") if isinstance(header, dict) else None
        if isinstance(cost, bool) or not isinstance(cost, int | float) or not math.isfinite(cost):
            raise ValueError(f"{path.name} has no cost_this_run_usd in its header")
        total += cost
    return total
