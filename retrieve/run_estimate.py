"""A pre-run cost estimate from what each question cost in a committed run (Step 13).

``eval.run`` prints it before opening an arm, and ``--max-cost`` refuses a run
above it.  Each arm is priced from its own committed ``dev`` results file: a
question that file scored costs what it cost there (its question total plus its
judge calls); any other question costs its class's mean in the file, or the
file's most expensive question when the file has no question of its class.
Retrieval-only runs drop the answer stages and the judges.  The sum is
multiplied by ``MARGIN``, and nothing is assumed to replay from the cache, so the
estimate runs high: Step 12's token-count estimate came out 2.2 times the spend.
The estimate reads only the question's ID and class.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean

from ingest.provenance import REPO_ROOT
from retrieve.arm import EstimateQuestion

RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"
MARGIN = 1.5
ANSWER_STAGES: tuple[str, ...] = ("generation",)
# graph_global's reduce call writes the answer; its map calls run only when it answers.
GLOBAL_ANSWER_STAGES: tuple[str, ...] = ("global_map", "generation")
# Each arm's committed dev run, scored on every dev question (graph_global: the global,
# decline and unanswerable ones).
VECTOR_RUN = RUNS_DIR / "2026-10-05-vector-dev-96e8c69.json"
GRAPH_LOCAL_RUN = RUNS_DIR / "2026-10-14-graph_local-dev-375efac.json"
GRAPH_TRAVERSAL_RUN = RUNS_DIR / "2026-10-14-graph_traversal-dev-375efac.json"
GRAPH_GLOBAL_RUN = RUNS_DIR / "2026-10-16-graph_global-dev-5bb0799.json"


@dataclass(frozen=True)
class _Measured:
    class_: str
    retrieval_usd: float
    full_usd: float  # retrieval, answer and judges


@dataclass(frozen=True)
class MeasuredEstimate:
    """Prices a run from *run_file*'s per-question costs; *answer_stages* are the cost
    entries a retrieval-only run does not spend."""

    run_file: Path
    answer_stages: tuple[str, ...] = ANSWER_STAGES
    margin: float = MARGIN

    def __call__(self, questions: Sequence[EstimateQuestion], *, retrieval_only: bool) -> float:
        costs = {qid: (m.class_, m.retrieval_usd if retrieval_only else m.full_usd)
                 for qid, m in self._read().items()}
        by_class: dict[str, list[float]] = defaultdict(list)
        for class_, usd in costs.values():
            by_class[class_].append(usd)
        unseen = {class_: fmean(values) for class_, values in by_class.items()}
        highest = max(usd for _, usd in costs.values())

        def priced(question: EstimateQuestion) -> float:
            if question.id in costs:
                return costs[question.id][1]
            return unseen.get(question.class_, highest)

        return sum(priced(q) for q in questions) * self.margin

    def _read(self) -> Mapping[str, _Measured]:
        try:
            document = json.loads(self.run_file.read_text(encoding="utf-8"))
            measured = {q["id"]: self._measured(q) for q in document["questions"]}
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"cannot estimate from {self.run_file.name}: "
                             f"{type(exc).__name__}: {exc}") from exc
        if not measured:
            raise ValueError(f"cannot estimate from {self.run_file.name}: it has no questions")
        return measured

    def _measured(self, question: Mapping) -> _Measured:
        cost = question["cost_usd"]
        judged = question.get("judged") or {}
        retrieval = cost["total"] - sum(cost.get(stage, 0.0) for stage in self.answer_stages)
        return _Measured(str(question["class"]), float(retrieval),
                         float(cost["total"]) + float(judged.get("cost_usd") or 0.0))
