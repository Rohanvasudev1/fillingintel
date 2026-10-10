"""Total extraction spend, summed over the committed run reports (Step 9).

Step 9's extraction runs are capped at TOTAL_CAP_USD in all. The total is the
sum of `cost_this_run_usd` (what the run's cache misses cost) over every run
report in the reports folder (benchmarks/extraction, committed) built under the
current ontology version; Step 8's run, under version 2, falls outside it. A
local report not yet committed counts too, which errs high. Calls made outside
`extract.run`, such as recording test replies, are not counted. Summing the
reports, rather than keeping a separate ledger, means the total can never
disagree with them.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from graph.ontology import ONTOLOGY_VERSION

TOTAL_CAP_USD = 40.0
_RUN_REPORT = re.compile(r"\d{10}-\d{2}-\d{6}-run\d+\.json")


def spent_usd(reports_dir: Path, ontology_version: int = ONTOLOGY_VERSION) -> float:
    """What the runs reported in *reports_dir* under *ontology_version* cost, in USD.

    Raises ValueError naming a report that cannot be read or has no valid cost.
    """
    if not reports_dir.is_dir():
        return 0.0
    total = 0.0
    for path in sorted(reports_dir.iterdir()):
        if not _RUN_REPORT.fullmatch(path.name):
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read run report {path.name}: {type(exc).__name__}") from exc
        if not isinstance(report, dict) or report.get("ontology_version") != ontology_version:
            continue
        cost = report.get("cost_this_run_usd")
        if isinstance(cost, bool) or not isinstance(cost, int | float) or not math.isfinite(cost):
            raise ValueError(f"run report {path.name} has no cost_this_run_usd")
        total += cost
    return total
