"""README block 2: the numbers most projects don't publish (Step 14 ticket 03).

One row per figure: value, label, source file with its commit, and a link to the
``results.md`` section that prints it in full.  Values and labels are read from
the report files ``eval.results_numbers`` and the router section read, through
the same helpers, so the README and ``results.md`` cannot disagree.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from eval.results_calibration import KIND_NAMES
from eval.results_inputs import (
    CORRUPTION_RUN,
    CYPHER_VALIDITY,
    EXTRACTION_ROUNDS,
    GLOBAL_PROVENANCE,
    PERSISTENCE_SCORE,
    RESOLUTION_SCORES,
)
from eval.results_numbers import extraction_counts, round_summary, scored, source_text
from eval.results_render import row_text
from eval.results_router import router_metrics
from eval.results_sources import RunFile, read_json
from ingest.provenance import REPO_ROOT

RESULTS_LINK = "benchmarks/results.md"
WRONG_CITATION = "wrong_citation"  # faithfulness reads no citations: that row is a control


@dataclass(frozen=True)
class Row:
    figure: str
    value: str
    label: str
    source: str  # file and commit
    anchor: str  # the results.md section that prints it in full

    def line(self) -> str:
        return row_text([self.figure, self.value, self.label, self.source,
                         f"[results.md]({RESULTS_LINK}#{self.anchor})"])


def _labelled_pairs(score: dict) -> str:
    provenance = score["provenance"]
    pairs = sum(s["labelled"] for s in score["strata"])
    return f"{provenance['note']}, {pairs} pairs, set `{provenance['set']}`"


def _resolution(root: Path) -> list[Row]:
    score, info = read_json(RESOLUTION_SCORES[-1], root)
    label, source = _labelled_pairs(score), source_text(info)
    adjudicator = score["adjudicator"]
    return [*(Row(f"Entity resolution {name}", scored(score[name]), label, source,
                  "entity-resolution") for name in ("precision", "recall")),
            Row("Entity resolution adjudicator accuracy",
                f"{adjudicator['correct']} of {adjudicator['judged']}, "
                f"{scored(adjudicator['accuracy'])}", label, source, "entity-resolution")]


def _cypher(root: Path) -> list[Row]:
    report, info = read_json(CYPHER_VALIDITY[0], root)
    pooled = report["rates"]["pooled"]
    label = f"dev, agent-drafted, `{report['prompt']['version']}`"
    return [Row(f"Cypher validity, {name}",
                f"{pooled[key + '_count']} of {pooled['n']} ({pooled[key]:.3f})", label,
                source_text(info), "cypher-validity")
            for name, key in (("first pass", "first_pass_valid"),
                              ("after one repair", "valid_after_repair"))]


def _router(router_run: RunFile) -> list[Row]:
    metrics = router_metrics(router_run)
    accuracy = metrics["accuracy"]
    text = (f"{accuracy['correct']} of {accuracy['n']}, {accuracy['value']:.3f} "
            f"[{accuracy['low']:.3f}, {accuracy['high']:.3f}] (Wilson)")
    return [Row("Router accuracy on `test`", text, f"{metrics['label']}",
                source_text(router_run.input), "router-accuracy-on-test")]


def _extraction(root: Path) -> list[Row]:
    counts = extraction_counts(root)
    source = f"latest `extract/v2` run of each of {counts.filings} filings at {counts.commits}"
    label = f"candidate triples over {counts.filings} filings"
    rows = [Row("Extraction ontology violations reaching the write path",
                f"{counts.violations} of {counts.candidates:,} ({counts.violation_rate:.3f})",
                label, source, "extraction"),
            Row("Extraction candidates rejected before the write path",
                f"{counts.rejected:,} of {counts.candidates:,} ({counts.rejection_rate:.3f})",
                label, source, "extraction")]
    for name, path in EXTRACTION_ROUNDS:
        summary, info = round_summary(path, root)
        low, high = summary["wilson_95"]
        rows.append(Row(f"Extraction review round {name}",
                        f"{summary['accuracy']:.3f} [{low:.3f}, {high:.3f}] (Wilson)",
                        f"the user's judgment of {summary['judged']} sampled edges",
                        source_text(info), "extraction"))
    return rows


def _persistence(root: Path) -> list[Row]:
    score, info = read_json(PERSISTENCE_SCORE, root)
    return [Row(f"Risk-factor persistence {name}", scored(score[name]), _labelled_pairs(score),
                source_text(info), "risk-factor-persistence-links")
            for name in ("precision", "recall")]


def _provenance(root: Path) -> list[Row]:
    rows = []
    for path in GLOBAL_PROVENANCE:
        report, info = read_json(path, root)
        split = "test" if "-test-" in report["run_file"] else "dev"
        rows.append(Row(f"graph_global citations that do not trace to a selected community "
                        f"({split})", f"{report['breaks']} breaks in {report['citations']}",
                        "agent-drafted questions", source_text(info),
                        "graph_global-provenance"))
    return rows


def _corruption(root: Path) -> list[Row]:
    run, info = read_json(CORRUPTION_RUN, root)
    label = f"{run['label']}, {len(run['answers'])} answers, {run['questions']}"
    rows = []
    for kind, row in run["catch_rates"].items():
        faith, cite = row["faithfulness"], row["citation_support"]
        control = " (a control: faithfulness ignores citations)" \
            if kind == WRONG_CITATION else ""
        rows.append(Row(f"Judge catches a planted {KIND_NAMES.get(kind, kind)}",
                        f"faithfulness {faith['caught']} of {faith['scored']}; citation "
                        f"support {cite['caught']} of {cite['scored']}{control}",
                        label, source_text(info), "judge-calibration"))
    return rows


def numbers_block(test_router: RunFile, root: Path = REPO_ROOT) -> list[str]:
    """*test_router* is the pre-registered router's ``test`` run."""
    rows = [*_resolution(root), *_cypher(root), *_router(test_router), *_extraction(root),
            *_persistence(root), *_provenance(root), *_corruption(root)]
    return [row_text(["figure", "value", "label", "source", "detail"]),
            row_text(["---"] * 5), *(r.line() for r in rows)]
