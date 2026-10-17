"""The "numbers most projects don't publish" block of ``benchmarks/results.md``.

Each function reads its committed report(s) and returns markdown lines plus the
``InputFile``s it read.  Every line names its file, commit and label.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from eval.results_inputs import (
    CYPHER_VALIDITY,
    EXTRACTION_DIR,
    EXTRACTION_ROUNDS,
    GLOBAL_HANDOFF,
    GLOBAL_PROVENANCE,
    PERSISTENCE_SCORE,
    RESOLUTION_SCORES,
)
from eval.results_sources import InputFile, ResultsInputError, read_json, read_jsonl
from ingest.provenance import REPO_ROOT

Block = tuple[list[str], list[InputFile]]
_EXTRACT_RUN = re.compile(r"(\d{10}-\d{2}-\d{6})-run(\d+)\.json")
_EXTRACT_V2 = "extract/v2@"


def _interval(value: float, interval: Mapping[str, float] | None) -> str:
    if not interval:
        return f"{value:.3f}"
    return f"{value:.3f} [{interval['low']:.3f}, {interval['high']:.3f}]"


def _source(info: InputFile) -> str:
    return f"`{info.path}` at `{info.commit}`"


def cypher_validity(root: Path = REPO_ROOT) -> Block:
    lines = ["| prompt | n | first pass valid | valid after repair | non-empty | fallback | "
             "source |", "|---|---|---|---|---|---|---|"]
    inputs = []
    for path in CYPHER_VALIDITY:
        report, info = read_json(path, root)
        pooled = report["rates"]["pooled"]
        lines.append(
            f"| {report['prompt']['version']} | {pooled['n']} | "
            + " | ".join(f"{pooled[k + '_count']} ({pooled[k]:.3f})" for k in
                         ("first_pass_valid", "valid_after_repair", "non_empty", "fallback"))
            + f" | {_source(info)} |")
        inputs.append(info)
    lines.append("")
    lines.append("Agent-drafted questions, dev, all classes. `cypher/v1` is the one the "
                 "`graph_traversal` arm uses: `cypher/v2` was more often valid but retrieved "
                 "worse (Step 11).")
    return lines, inputs


def resolution(root: Path = REPO_ROOT) -> Block:
    lines = ["| round | precision | recall | adjudicator accuracy | source |",
             "|---|---|---|---|---|"]
    inputs = []
    for number, path in enumerate(RESOLUTION_SCORES, start=1):
        score, info = read_json(path, root)
        adjudicator = score["adjudicator"]["accuracy"]
        lines.append(
            f"| {number} ({score['provenance']['set']}) | "
            f"{_interval(score['precision']['value'], score['precision']['interval'])} | "
            f"{_interval(score['recall']['value'], score['recall']['interval'])} | "
            f"{_interval(adjudicator['value'], adjudicator['interval'])} | {_source(info)} |")
        inputs.append(info)
    lines += ["", "Pairwise, weighted by stratum, fixed-seed bootstrap 95% intervals; "
                  "agent-labelled (gpt-6-luna). Rounds 1 and 2 are different draws, not a "
                  "paired comparison; round 2 (group cut) is the current figure."]
    return lines, inputs


def extraction(root: Path = REPO_ROOT) -> Block:
    lines = ["| review round | filing | judged | accuracy [Wilson 95%] | prompt | source |",
             "|---|---|---|---|---|---|"]
    inputs = []
    for name, path in EXTRACTION_ROUNDS:
        rows, info = read_jsonl(path, root)
        summary = next((r for r in rows if r.get("kind") == "summary"), None)
        if summary is None:
            raise ResultsInputError(f"{path} has no summary line")
        low, high = summary["wilson_95"]
        lines.append(f"| {name} | {Path(path).name.split('-round')[0]} | {summary['judged']} | "
                     f"{summary['accuracy']:.3f} [{low:.3f}, {high:.3f}] | "
                     f"{', '.join(summary['prompt_versions'])} | {_source(info)} |")
        inputs.append(info)
    runs, run_inputs = _latest_v2_runs(root)
    candidates = sum(r["candidates"] for r in runs)
    rejected = sum(r["rejections"]["total"] for r in runs)
    violations = sum(len(r["violations"]) for r in runs)
    commits = ", ".join(f"`{c}`" for c in sorted({i.commit for i in run_inputs}))
    lines += ["",
              "Accuracy is the user's judgment of 30 sampled edges per round (Step 8 round 2: "
              "NVIDIA FY2026 10-K, claude-sonnet-5-5; Step 9 round 3: AMD FY2025 10-K, "
              "claude-haiku-5-5). The two rounds judge different models and prompts and are not "
              "compared.",
              "",
              f"Over the latest `extract/v2` run of each of the {len(runs)} filings "
              f"(`{EXTRACTION_DIR}/*-run*.json`, commit {commits}): {violations} ontology "
              f"violations reached the write path out of {candidates:,} candidate triples; "
              f"{rejected} candidates were rejected before it ({rejected / candidates:.3f})."]
    return lines, inputs + run_inputs


def _latest_v2_runs(root: Path) -> tuple[list[dict], list[InputFile]]:
    latest: dict[str, tuple[int, str]] = {}
    for path in sorted((root / EXTRACTION_DIR).glob("*-run*.json")):
        match = _EXTRACT_RUN.fullmatch(path.name)
        if match is None:
            continue
        accession, number = match[1], int(match[2])
        if accession not in latest or number > latest[accession][0]:
            latest[accession] = (number, f"{EXTRACTION_DIR}/{path.name}")
    runs, inputs = [], []
    for accession in sorted(latest):
        report, info = read_json(latest[accession][1], root)
        if not str(report["prompt"].get("prompt_version", "")).startswith(_EXTRACT_V2):
            raise ResultsInputError(f"{info.path} is not an extract/v2 run")
        runs.append(report)
        inputs.append(info)
    return runs, inputs


def persistence(root: Path = REPO_ROOT) -> Block:
    score, info = read_json(PERSISTENCE_SCORE, root)
    return ([f"Precision {_interval(score['precision']['value'], score['precision']['interval'])}"
             f", recall {_interval(score['recall']['value'], score['recall']['interval'])}; "
             f"agent-labelled (gpt-6-luna); {_source(info)}."], [info])


def provenance(root: Path = REPO_ROOT) -> Block:
    report, info = read_json(GLOBAL_PROVENANCE, root)
    strict = report["strict"]
    return ([f"`graph_global` dev run `{report['run_file']}`: {report['citations']} kept "
             f"citations, {report['breaks']} breaks through any selected community (the gate); "
             f"{strict['breaks']} breaks when each citation must trace through the community "
             f"of the kept map point that cited it. Agent-drafted questions; {_source(info)}."],
            [info])


def handoff(root: Path = REPO_ROOT) -> Block:
    report, info = read_json(GLOBAL_HANDOFF, root)
    lines = [f"`graph_global` chunk handoff, {report['label']} (the arm is unchanged), "
             f"{report['questions']}; {_source(info)}.", "",
             "| class | n | as built recall@5 | as built recall@10 | handoff recall@5 | "
             "handoff recall@10 |", "|---|---|---|---|---|---|"]
    for class_, built in report["scores"]["as_built"].items():
        other = report["scores"]["handoff"][class_]
        lines.append(f"| {class_} | {built['n']} | {built['recall@5']:.3f} | "
                     f"{built['recall@10']:.3f} | {other['recall@5']:.3f} | "
                     f"{other['recall@10']:.3f} |")
    return lines, [info]


SECTIONS = (
    ("Cypher validity", cypher_validity),
    ("Entity resolution", resolution),
    ("Extraction", extraction),
    ("Risk-factor persistence links", persistence),
    ("graph_global provenance", provenance),
    ("graph_global chunk-handoff ablation", handoff),
)


def numbers_block(root: Path = REPO_ROOT) -> Block:
    lines, inputs = [], []
    for title, section in SECTIONS:
        body, read = section(root)
        lines += [f"### {title}", "", *body, ""]
        inputs += read
    return lines, inputs
