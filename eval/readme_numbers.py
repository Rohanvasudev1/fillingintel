"""README block 2: the numbers most projects don't publish (Step 14 ticket 03).

One row per figure: value, label, source file with its commit, and a link to the
``results.md`` section that prints it in full.  Every value is read from the
report files ``eval.results_numbers`` and the router section read.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
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
from eval.results_numbers import latest_v2_runs
from eval.results_router import router_metrics
from eval.results_sources import InputFile, RunFile, read_json, read_jsonl
from ingest.provenance import REPO_ROOT

RESULTS_LINK = "benchmarks/results.md"
AGENT_LABELLED = "agent-labelled (gpt-6-luna)"
Row = tuple[str, str, str, str, str]  # figure, value, label, source, section anchor


def _interval(block: Mapping) -> str:
    interval = block.get("interval")
    text = f"{block['value']:.3f}"
    return f"{text} [{interval['low']:.3f}, {interval['high']:.3f}]" if interval else text


def _source(info: InputFile) -> str:
    return f"`{info.path}` at `{info.commit}`"


def _resolution(root: Path) -> list[Row]:
    score, info = read_json(RESOLUTION_SCORES[-1], root)
    label = f"{AGENT_LABELLED}, 100 pairs, round 2 (group cut)"
    return [(f"Entity resolution {name}", _interval(score[name]), label, _source(info),
             "entity-resolution") for name in ("precision", "recall")]


def _cypher(root: Path) -> list[Row]:
    report, info = read_json(CYPHER_VALIDITY[0], root)
    pooled = report["rates"]["pooled"]
    label = f"dev, agent-drafted, `{report['prompt']['version']}` (the one the arm uses)"
    return [(f"Cypher validity, {name}",
             f"{pooled[key + '_count']} of {pooled['n']} ({pooled[key]:.3f})", label,
             _source(info), "cypher-validity")
            for name, key in (("first pass", "first_pass_valid"),
                              ("after one repair", "valid_after_repair"))]


def _router(router_run: RunFile) -> list[Row]:
    accuracy = router_metrics(router_run)["accuracy"]
    text = (f"{accuracy['correct']} of {accuracy['n']}, {accuracy['value']:.3f} "
            f"[{accuracy['low']:.3f}, {accuracy['high']:.3f}] (Wilson)")
    return [("Router accuracy on `test`", text, "against agent-drafted class labels",
             _source(router_run.input), "router-accuracy-on-test")]


def _extraction(root: Path) -> list[Row]:
    runs, inputs = latest_v2_runs(root)
    candidates = sum(r["candidates"] for r in runs)
    rejected = sum(r["rejections"]["total"] for r in runs)
    violations = sum(len(r["violations"]) for r in runs)
    commits = ", ".join(f"`{c}`" for c in sorted({i.commit for i in inputs}))
    source = f"latest `extract/v2` run of each of {len(runs)} filings at {commits}"
    rows: list[Row] = [
        ("Extraction ontology violations reaching the write path",
         f"{violations} of {candidates:,} ({violations / candidates:.3f})",
         "candidate triples, all 24 filings", source, "extraction"),
        ("Extraction candidates rejected before the write path",
         f"{rejected:,} of {candidates:,} ({rejected / candidates:.3f})",
         "mostly spans not found in their chunk, and edges to a rejected node", source,
         "extraction")]
    for name, path in EXTRACTION_ROUNDS:
        lines, info = read_jsonl(path, root)
        summary = next(r for r in lines if r.get("kind") == "summary")
        low, high = summary["wilson_95"]
        rows.append((f"Extraction review round {name}",
                     f"{summary['accuracy']:.3f} [{low:.3f}, {high:.3f}] (Wilson)",
                     f"the user's judgment of {summary['judged']} sampled edges",
                     _source(info), "extraction"))
    return rows


def _persistence(root: Path) -> list[Row]:
    score, info = read_json(PERSISTENCE_SCORE, root)
    return [(f"Risk-factor persistence {name}", _interval(score[name]),
             f"{AGENT_LABELLED}, 100 pairs", _source(info), "risk-factor-persistence-links")
            for name in ("precision", "recall")]


def _provenance(root: Path) -> list[Row]:
    rows = []
    for path in GLOBAL_PROVENANCE:
        report, info = read_json(path, root)
        split = "test" if "-test-" in report["run_file"] else "dev"
        rows.append((f"graph_global citations that do not trace to a selected community "
                     f"({split})", f"{report['breaks']} breaks in {report['citations']}",
                     "agent-drafted questions", _source(info), "graph_global-provenance"))
    return rows


def _corruption(root: Path) -> list[Row]:
    run, info = read_json(CORRUPTION_RUN, root)
    rows = []
    for kind, row in run["catch_rates"].items():
        faith, cite = row["faithfulness"], row["citation_support"]
        control = " (a control: faithfulness ignores citations)" if kind == "wrong_citation" \
            else ""
        rows.append((f"Judge catches a planted {KIND_NAMES.get(kind, kind)}",
                     f"faithfulness {faith['caught']} of {faith['scored']}; citation support "
                     f"{cite['caught']} of {cite['scored']}{control}",
                     "corruption test on 30 dev answers, not calibration", _source(info),
                     "judge-calibration"))
    return rows


def numbers_rows(test_router: RunFile, root: Path = REPO_ROOT) -> list[Row]:
    return [*_resolution(root), *_cypher(root), *_router(test_router), *_extraction(root),
            *_persistence(root), *_provenance(root), *_corruption(root)]


def numbers_block(test_router: RunFile, root: Path = REPO_ROOT) -> list[str]:
    """*test_router* is the pre-registered router's ``test`` run."""
    lines = ["| figure | value | label | source | detail |", "|---|---|---|---|---|"]
    lines += [_line(row) for row in numbers_rows(test_router, root)]
    return lines


def _line(row: Sequence[str]) -> str:
    figure, text, label, source, anchor = row
    return (f"| {figure} | {text} | {label} | {source} | "
            f"[results.md]({RESULTS_LINK}#{anchor}) |")
