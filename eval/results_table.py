"""``python -m eval.results_table``: build ``benchmarks/results.md`` from committed files
(Step 13, ticket 02).

Offline: it reads only the files ``eval.results_inputs`` names, with no network
or database call, and the same files give the same bytes.  Sections, in order:
the ``test`` tables when ``test`` runs exist (ticket 05), paired differences
against ``vector``, H1–H5 verdicts (on ``test``, else on ``dev`` labelled as
tuning data) and H6 (on the dev router run), the router section (classifier
runs, router accuracy, unanswerable questions, the oracle and the dev-best
map), the other measured numbers, and the ``dev`` tables, labelled as tuning
data.  The router rows join the per-split tables and paired differences but
never the dev-best rule or H1–H5.

Usage::

    uv run python -m eval.results_table            # write benchmarks/results.md
    uv run python -m eval.results_table --check    # exit 4 if the committed file differs
    uv run python -m eval.results_table --dev-best # write the dev-best map, once

Exit codes: 0 written or matching; 2 bad arguments or the map already exists;
3 an input is missing or inconsistent; 4 ``--check`` found a difference.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from eval import hypotheses as h
from eval.bootstrap import percentile
from eval.compare import CompareError
from eval.dev_best import (
    RULE,
    DevBestError,
    dev_best_choices,
    map_document,
    read_map,
    write_map,
)
from eval.results_calibration import (
    Calibration,
    calibration_block,
    dropped_metrics,
    read_calibration,
)
from eval.results_inputs import (
    CALIBRATION_REPORT,
    CONTROL_ARM,
    CORRUPTION_RUN,
    DEV,
    DEV_BEST_MAP,
    DEV_ROUTER_RUNS,
    DEV_RUNS,
    RESULTS_MD,
    ROUTE_RUNS,
    ROUTER_ROWS,
    TEST,
    TEST_ROUTER_RUNS,
    TEST_RUNS,
    RunNames,
)
from eval.results_numbers import numbers_block
from eval.results_paired import difference_text, paired_difference
from eval.results_render import answerable_table, judged_labels, no_gold_table, paired_table
from eval.results_router import (
    accuracy_lines,
    oracle_lines,
    router_accuracy,
    sweep_table,
    unanswerable_lines,
)
from eval.results_sources import (
    InputFile,
    ResultsInputError,
    Split,
    load_router_split,
    load_split,
    read_json,
    split_inputs,
)
from eval.schema import NO_GOLD_CLASSES
from ingest.provenance import REPO_ROOT, git_state
from retrieve.routing_maps import PRE_REGISTERED

EXIT_OK, EXIT_USAGE, EXIT_COULD_NOT_RUN, EXIT_DIFFERS = 0, 2, 3, 4
TITLE = "# FilingIntel benchmark results"
GENERATED = ("Built by `uv run python -m eval.results_table` from the committed files listed "
             "below; CI rebuilds it and fails on any difference. Do not edit by hand.")
QUESTIONS_NOTE = ("**Every number here is on agent-drafted questions**: 140 questions agents "
                  "wrote with their gold chunks, not reviewed by a human "
                  "(`eval/agent_drafted_set.jsonl`). Judged columns come from gpt-6-luna; each "
                  "column's header says whether it passed calibration against the user's own "
                  "scores (see Judge calibration).")
GLOBAL_COST_NOTE = ("graph_global's dev cost per question comes from its full run, which answered "
                    "only global questions; its other classes were scored retrieval only.")
TUNING_NOTE = ("Tuning data: every choice (hop count, Cypher prompt, N, the dev-best map) "
               "was made on these questions.")


ROUTER_NOTE = ("The router rows are not arms: the router classifies each question with "
               "claude-haiku-5-5, declines investment advice itself and hands the rest to the "
               "arm its routing map names. The dev-best map is tuned on dev.")


def _split_section(name: str, split: Split, tuning: bool, calibration: Calibration
                   ) -> list[str]:
    """*split* holds the arms, then any router rows."""
    note = [TUNING_NOTE, ""] if tuning else []
    fallback = judged_labels(split)
    labels = calibration.column_labels(fallback)
    dropped = dropped_metrics(labels)
    note += [*calibration.failed_notes(), ""] if dropped else []
    if any(label in split for label in ROUTER_ROWS.values()):
        note += [ROUTER_NOTE, ""]
    return [f"## `{name}` results", "", *note,
            "Answerable classes. Intervals are percentile bootstrap 95% over the cell's "
            "questions; a cell under 10 questions is directional.", "",
            *answerable_table(split, labels), "",
            "Decline and unanswerable questions: whether the answer declined or said the "
            "filings lack the evidence.", "",
            *no_gold_table(split, calibration.no_gold_label(fallback)), "",
            f"### `{name}` paired differences against `vector`", "",
            "On the questions both runs hold, as `eval.compare` computes them; a difference "
            "counts only when its interval excludes 0. graph_global's judged differences "
            "compare whole pipelines (its reduce prompt is not `answer/v1`).", "",
            *paired_table(split, dropped), ""]


def verdicts(split: Split, dropped: Sequence[str] = ()) -> list[h.Verdict]:
    """H1-H5; a hypothesis read on a judged metric in *dropped* (failed calibration) is
    not testable."""
    graph_arms = [a for a in split if a != CONTROL_ARM]
    control = split[CONTROL_ARM]

    def diff(arm: str, class_: str, metric: str):
        base, other = control.source(class_), split[arm].source(class_)
        if base is None or other is None:
            return None
        return paired_difference(base, other, class_, metric)

    h1 = {a: d for a in graph_arms if (d := diff(a, "lookup", "metrics.recall@5"))}
    return [
        h.h1(h1),
        h.h2(diff("graph_traversal", "multi_hop", "metrics.recall@10")
             if "graph_traversal" in split else None),
        h.h3({arm: _wrong_relations(split, arm) for arm in split}),
        _h4(split, diff, dropped),
        _h5(split, graph_arms),
    ]


def _h4(split: Split, diff, dropped: Sequence[str]) -> h.Verdict:
    if "answer_relevancy" in dropped:
        return h.Verdict(h.H4_RULE, h.NOT_TESTABLE,
                         ("answer relevancy failed calibration, so it is not read",))
    return h.h4(diff("graph_global", "global", "judged.metrics.answer_relevancy.mean")
                if "graph_global" in split else None)


def _wrong_relations(split: Split, arm: str) -> list[list[str]]:
    runs = split[arm]
    found = []
    for class_ in dict.fromkeys(c for f in runs.files for c in f.classes):
        source = runs.source(class_)
        if class_ in NO_GOLD_CLASSES or source is None:
            continue
        found += [[hit["relation"] for hit in q["wrong_evidence"]]
                  for q in source.questions() if q["class"] == class_ and q["wrong_evidence"]]
    return found


def _h5(split: Split, graph_arms: Sequence[str]) -> h.Verdict:
    def answered(arm: str) -> dict[str, Mapping]:
        return {q["id"]: q for f in split[arm].full_files() for q in f.questions()}

    control = answered(CONTROL_ARM)
    mine, theirs = {}, {}
    for arm in graph_arms:
        own = answered(arm)
        shared = [qid for qid in own if qid in control]
        if shared:
            mine[arm] = _latency_cost([own[q] for q in shared])
            theirs[arm] = _latency_cost([control[q] for q in shared])
    return h.h5(theirs, mine)


def _latency_cost(questions: Sequence[Mapping]) -> h.ArmLatencyCost:
    totals = [q.get("total_ms") for q in questions]
    p95 = None if any(t is None for t in totals) else percentile(totals, 0.95)
    cost = sum(float(q["cost_usd"]["total"]) for q in questions) / len(questions)
    return h.ArmLatencyCost(p95, cost, len(questions))


def _h6(dev_routers: Split) -> h.Verdict:
    runs = dev_routers.get(ROUTER_ROWS[PRE_REGISTERED])
    files = runs.full_files() if runs is not None else ()
    if len(files) != 1:
        return h.h6(None)
    return h.h6(router_accuracy(files[0]))


def _verdict_section(split_name: str, split: Split, dev_routers: Split,
                     calibration: Calibration) -> list[str]:
    lines = [f"## Hypotheses (read on `{split_name}`)", ""]
    if split_name == DEV:
        lines += [f"No `test` runs yet: these verdicts read `dev`. {TUNING_NOTE}", ""]
    lines += ["H6 reads the pre-registered router's `dev` run, as the plan writes it.", ""]
    dropped = dropped_metrics(calibration.column_labels(judged_labels(split)))
    for verdict in [*verdicts(split, dropped), _h6(dev_routers)]:
        lines += [f"- **{verdict.verdict}**: {verdict.rule}.",
                  *(f"  - {r}" for r in verdict.readings)]
    return [*lines, ""]


def _router_section(dev: Split, dev_routers: Split, root: Path
                    ) -> tuple[list[str], list[InputFile]]:
    lines, inputs = _routing_lines(dev, dev_routers, root)
    choices = dev_best_choices(dev)
    computed = {c.class_: c.chosen for c in choices}
    lines += ["### Dev-best routing map (tuned on dev)", "", f"Rule: {RULE}.", ""]
    if (root / DEV_BEST_MAP).exists():
        document, info = read_json(DEV_BEST_MAP, root)
        if read_map(document) != computed:
            raise ResultsInputError(f"{DEV_BEST_MAP} holds {read_map(document)} but the rule "
                                    f"gives {computed} on the committed dev runs")
        inputs.append(info)
        lines += [f"Written to `{DEV_BEST_MAP}` at `{info.commit or 'its commit'}`.", ""]
    else:
        lines += [f"`{DEV_BEST_MAP}` is not written yet (`--dev-best`).", ""]
    lines += ["| class | arm | recall@10 | difference vs best [95%] | tied | dev cost / question "
              "(USD) | chosen |", "|---|---|---|---|---|---|---|"]
    for choice in choices:
        for c in choice.contenders:
            diff = "best" if c.difference is None else difference_text(c.difference)
            lines.append(f"| {choice.class_} | {c.arm} | {c.recall_at_10:.3f} | {diff} | "
                         f"{'yes' if c.tied else 'no'} | {c.cost_per_question:.4f} | "
                         f"{'**yes**' if c.arm == choice.chosen else ''} |")
    return [*lines, "", GLOBAL_COST_NOTE, ""], inputs


def _routing_lines(dev: Split, dev_routers: Split, root: Path
                   ) -> tuple[list[str], list[InputFile]]:
    """The classifier runs, router accuracy, unanswerable questions and the oracle, on dev."""
    route_runs, inputs = [], []
    for path in ROUTE_RUNS:
        document, info = read_json(path, root)
        route_runs.append((document, path))
        inputs.append(info)
    lines = ["## Router", "", ROUTER_NOTE, "",
             "### Classifier on `dev` (`eval.route`, no arm run)", "",
             "The effort sweep under `route/v1` (the most accurate effort wins, ties to the "
             "lower), then at most one revision at that effort, driven by the sweep's confusion "
             "matrices (in the route run files). The more accurate version is the one the "
             "router uses. Accuracy is against agent-drafted class labels.", ""]
    pre = dev_routers.get(ROUTER_ROWS[PRE_REGISTERED])
    if pre is None or len(pre.full_files()) != 1:
        return [*lines, "No router run yet.", ""], inputs
    run = pre.full_files()[0]
    router = run.header["router"]
    lines += [*sweep_table(route_runs, (router["prompt"]["version"], router["effort"])), "",
              "### Router accuracy on `dev`", "", *accuracy_lines(run),
              "### Unanswerable questions under the router", "",
              "Unanswerable questions are left out of accuracy: no class names them, so the "
              "question is whether the arm they reach says the filings lack the answer.", "",
              *unanswerable_lines(dev_routers), "",
              "### Oracle router on `dev`", "",
              "The oracle gives each answerable question its gold class and the pre-registered "
              "map's arm, reading that arm's own dev run, so it costs nothing. The gap between "
              "the oracle and the pre-registered router is misclassification, plus any "
              "difference between the router's run of an arm and that arm's own run (an arm "
              "whose model calls missed the cache reran them; see each run's commit and "
              "graph load run).", "",
              *oracle_lines(dev, dev_routers), ""]
    return lines, inputs


def _inputs_table(inputs: Sequence[InputFile]) -> list[str]:
    unique = list({i.path: i for i in inputs}.values())
    return ["| file | sha256 | commit |", "|---|---|---|",
            *(f"| `{i.path}` | `{i.sha256}` | {f'`{i.commit}`' if i.commit else '-'} |"
              for i in sorted(unique, key=lambda i: i.path)), ""]


def build(root: Path = REPO_ROOT, dev_runs: RunNames = DEV_RUNS,
          test_runs: RunNames = TEST_RUNS, dev_router_runs: RunNames = DEV_ROUTER_RUNS,
          test_router_runs: RunNames = TEST_ROUTER_RUNS,
          calibration: tuple[str, str] = (CALIBRATION_REPORT, CORRUPTION_RUN)) -> str:
    """The markdown of ``benchmarks/results.md``; *calibration* names the calibration
    report and the corruption run, each read only once it exists."""
    judge_check = read_calibration(*calibration, root)
    dev = load_split(dev_runs, root)
    test = load_split(test_runs, root) if test_runs else None
    dev_routers = load_router_split(dev_router_runs, ROUTER_ROWS, root)
    test_routers = load_router_split(test_router_runs, ROUTER_ROWS, root)
    body = []
    if test is not None:
        body += _split_section(TEST, {**test, **test_routers}, tuning=False,
                               calibration=judge_check)
    body += _verdict_section(TEST if test is not None else DEV, test or dev, dev_routers,
                             judge_check)
    router, router_inputs = _router_section(dev, dev_routers, root)
    numbers, number_inputs = numbers_block(root)
    body += [*router, "## Other measured numbers", "", *numbers,
             "### Judge calibration", "", *calibration_block(judge_check)]
    body += _split_section(DEV, {**dev, **dev_routers}, tuning=True, calibration=judge_check)
    inputs = [*split_inputs(dev), *(split_inputs(test) if test else []),
              *split_inputs(dev_routers), *split_inputs(test_routers), *router_inputs,
              *number_inputs, *judge_check.inputs]
    head = [TITLE, "", GENERATED, "", QUESTIONS_NOTE, "", "### Inputs", "",
            *_inputs_table(inputs)]
    return "\n".join([*head, *body]).rstrip("\n") + "\n"


def main(argv: Sequence[str] | None = None, *, root: Path = REPO_ROOT) -> int:
    parser = argparse.ArgumentParser(description="Build benchmarks/results.md offline.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="exit 4 when the committed file differs from a fresh build")
    mode.add_argument("--dev-best", action="store_true",
                      help=f"write {DEV_BEST_MAP} once from the dev runs")
    parser.add_argument("--output", type=Path, default=None,
                        help=f"the results file (default {RESULTS_MD})")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    output = args.output or root / RESULTS_MD
    try:
        if args.dev_best:
            dev = load_split(DEV_RUNS, root)
            write_map(map_document(dev_best_choices(dev), dev, git_state()),
                      root / DEV_BEST_MAP)
            print(f"wrote {DEV_BEST_MAP}")
            return EXIT_OK
        text = build(root)
    except DevBestError as exc:
        print(f"results table: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (ResultsInputError, CompareError, KeyError, TypeError) as exc:
        print(f"results table could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    if args.check:
        committed = output.read_text(encoding="utf-8") if output.exists() else None
        if committed != text:
            print(f"{output} differs from a fresh build; run python -m eval.results_table "
                  "and commit it", file=sys.stderr)
            return EXIT_DIFFERS
        print(f"{output} matches the committed runs")
        return EXIT_OK
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"wrote {output}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
