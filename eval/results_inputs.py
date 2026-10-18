"""The committed files ``benchmarks/results.md`` is built from (Step 13 ticket 02).

Named here, not globbed, so a stray local run never enters the table.  The one
exception is the extraction run reports: the latest ``extract/v2`` run of each of
the 24 filings is found in ``EXTRACTION_DIR``, which holds only committed files.  Each arm
lists its run files per split; an arm may hold a full run and a retrieval-only
run, and ``eval.results_sources`` picks, per class, the full run when one holds
the class.  Ticket 05 adds the ``test`` runs.
"""
from __future__ import annotations

from collections.abc import Mapping

ARMS = ("vector", "graph_local", "graph_traversal", "graph_global")
CONTROL_ARM = "vector"
DEV, TEST = "dev", "test"

RunNames = Mapping[str, tuple[str, ...]]  # arm -> file names under benchmarks/runs

DEV_RUNS: RunNames = {
    "vector": ("2026-10-05-vector-dev-96e8c69.json",),
    "graph_local": ("2026-10-14-graph_local-dev-375efac.json",),
    "graph_traversal": ("2026-10-14-graph_traversal-dev-375efac.json",),
    "graph_global": ("2026-10-16-graph_global-dev-5bb0799.json",
                     "2026-10-17-graph_global-dev-6e26741.json"),
}
TEST_RUNS: RunNames = {}
# The router's runs per split, keyed by routing map (Step 13 ticket 03); their rows are
# labelled ROUTER_ROWS[map] and never join the dev-best rule or H1-H5.
ROUTER_ROWS = {"pre_registered": "router (pre-registered)", "dev_best": "router (dev-best)"}
# Keyed by retrieve.routing_maps' map names; a test checks they agree.
DEV_ROUTER_RUNS: RunNames = {
    "pre_registered": ("2026-10-18-router-pre_registered-dev-c1b171d.json",),
    "dev_best": ("2026-10-18-router-dev_best-dev-c1b171d.json",),
}
TEST_ROUTER_RUNS: RunNames = {}
# eval.route's classifier runs: the effort sweep under route/v1, then route/v2.
ROUTE_RUNS = tuple(f"benchmarks/router/route-run{n}.json" for n in (1, 2, 3, 4))

# Reports for the "numbers most projects don't publish" block, by repo-relative path.
CYPHER_VALIDITY = ("benchmarks/graph/cypher-validity-run1.json",
                   "benchmarks/graph/cypher-validity-run2.json")
RESOLUTION_SCORES = ("benchmarks/resolution/scoring-score1.json",
                     "benchmarks/resolution/scoring2-score1.json")
# (round as BUILD-LOG names it, file): each file numbers its rounds per filing.
EXTRACTION_ROUNDS = (("2 (Step 8)", "benchmarks/extraction/0001045810-26-000021-round2.jsonl"),
                     ("3 (Step 9)", "benchmarks/extraction/0000002488-26-000018-round1.jsonl"))
EXTRACTION_DIR = "benchmarks/extraction"
PERSISTENCE_SCORE = "benchmarks/persistence/scoring-score1.json"
GLOBAL_PROVENANCE = "benchmarks/graph/global-provenance-run2.json"
GLOBAL_HANDOFF = "benchmarks/graph/global-handoff-run1.json"
DEV_BEST_MAP = "benchmarks/router/dev-best-map.json"
RESULTS_MD = "benchmarks/results.md"
