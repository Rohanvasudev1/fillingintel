"""The router section of ``benchmarks/results.md`` (Step 13 ticket 03).

From committed files only: the classifier runs of ``eval.route`` (the effort
sweep and the prompt revision), router accuracy with its Wilson interval and
the confusion matrix read from the pre-registered router's per-question
routing, false declines, the share of unanswerable questions each router
answered ``not_found``, and the oracle router.  The oracle gives each
answerable question its gold class and the pre-registered map's arm, reading
that arm's own run file (a retrieval-only file is marked so); comparing it
with the router separates misclassification from the map's choice.  Every
accuracy figure is "against agent-drafted class labels".
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import fmean

from eval.gate_scores import GATED_CLASSES
from eval.hypotheses import RouterAccuracy
from eval.results_render import row_text, value
from eval.results_sources import RETRIEVAL_ONLY, ArmRuns, ResultsInputError, RunFile, Split
from eval.route_metrics import LABEL, PREDICTED_ORDER, Classified, route_metrics
from retrieve.routing_maps import H6_MAP

RECALL_10 = "recall@10"
POOLED = "pooled"


def classified(run: RunFile) -> list[Classified]:
    """Each question's gold and predicted class in a router run."""
    rows = []
    for q in run.questions():
        routing = q.get("routing")
        if routing is None:
            raise ResultsInputError(f"{run.input.path}: {q['id']} has no routing record")
        rows.append(Classified(str(q["id"]), str(q["class"]), routing["predicted_class"]))
    return rows


def router_metrics(run: RunFile) -> dict[str, object]:
    """``eval.route_metrics`` over one router run's per-question routing."""
    return route_metrics(classified(run))


def router_accuracy(run: RunFile) -> RouterAccuracy:
    metrics = router_metrics(run)
    accuracy, declines = metrics["accuracy"], metrics["declines"]
    return RouterAccuracy(accuracy["correct"], accuracy["n"], declines["declined"],
                          declines["n"])


def sweep_table(route_runs: Sequence[tuple[Mapping, str]], frozen: tuple[str, str]
                ) -> list[str]:
    """One row per classifier run: prompt, effort, accuracy and spend; the frozen one marked."""
    lines = [row_text(["run", "prompt", "effort", "accuracy [Wilson 95%]", "declines declined",
                   "false declines", "spent (USD)", "commit", "router uses"]),
             row_text(["---"] * 9)]
    for document, path in route_runs:
        metrics = document["metrics"]
        accuracy = metrics["accuracy"]
        uses = (document["prompt"]["version"], document["effort"]) == frozen
        lines.append(row_text([
            f"`{path}`", f"`{document['prompt']['version']}`", document["effort"],
            f"{accuracy['correct']} of {accuracy['n']} "
            f"{value(accuracy['value'], {'low': accuracy['low'], 'high': accuracy['high']})}",
            f"{metrics['declines']['declined']} of {metrics['declines']['n']}",
            str(len(metrics["false_declines"])), f"{document['spent_usd']:.4f}",
            f"`{document['commit']}`", "**yes**" if uses else ""]))
    return lines


def accuracy_lines(run: RunFile) -> list[str]:
    """Accuracy, per-class accuracy, the confusion matrix and the declines of one router run."""
    metrics = route_metrics(classified(run))
    accuracy = metrics["accuracy"]
    columns = [c for c in PREDICTED_ORDER
               if any(c in row for row in metrics["confusion"].values())]
    lines = [f"Router accuracy ({LABEL}), from `{run.input.path}`: {accuracy['correct']} of "
             f"{accuracy['n']}, "
             f"{value(accuracy['value'], {'low': accuracy['low'], 'high': accuracy['high']})} "
             "(Wilson 95%). Answerable and decline questions count; a decline question counts "
             "only when the router declined it.", "",
             row_text(["gold \\ predicted", *columns, "accuracy"]),
             row_text(["---"] * (len(columns) + 2))]
    for gold, row in metrics["confusion"].items():
        per_class = metrics["per_class"][gold]
        lines.append(row_text([gold, *(str(row.get(c, 0)) for c in columns),
                           f"{per_class['correct']} of {per_class['n']}"]))
    declines = metrics["declines"]
    false = ", ".join(f"`{i}`" for i in metrics["false_declines"]) or "none"
    missed = ", ".join(f"`{i}`" for i in declines["missed"]) or "none"
    lines += ["", f"Decline questions declined: {declines['declined']} of {declines['n']} "
              f"(missed: {missed}). False declines (a question declined that is not a decline "
              f"question): {false}. Failed classifications: "
              f"{len(metrics['errors'])}.", ""]
    return lines


def unanswerable_lines(routers: Split) -> list[str]:
    """Per router run, how the unanswerable questions were routed and answered."""
    lines = [row_text(["router", "unanswerable n", "predicted classes", "answered not_found",
                   "declined"]), row_text(["---"] * 5)]
    for name, runs in routers.items():
        for run in runs.full_files():
            questions = [q for q in run.questions() if q["class"] == "unanswerable"]
            if not questions:
                continue
            shares = route_metrics(classified(run))["unanswerable"]["share"]
            status = [q["answer"]["status"] for q in questions]
            lines.append(row_text([
                name, str(len(questions)),
                ", ".join(f"{c} {s:.2f}" for c, s in shares.items()),
                f"{status.count('not_found')} of {len(questions)}",
                f"{status.count('declined')} of {len(questions)}"]))
    return lines


def _recall(questions: Sequence[Mapping]) -> float | None:
    values = [float(q["metrics"][RECALL_10]) for q in questions if q.get("metrics")]
    return fmean(values) if values else None


def oracle_lines(arms: Split, routers: Split) -> list[str]:
    """Recall@10 per answerable class: the oracle (gold class through the pre-registered map,
    from the mapped arm's own run) beside each router run."""
    names = list(routers)
    lines = [row_text(["class", "oracle arm", "oracle recall@10",
                       *(f"{n} recall@10" for n in names)]),
             row_text(["---"] * (3 + len(names)))]
    oracle_all: list[Mapping] = []
    router_all: dict[str, list[Mapping]] = {n: [] for n in names}
    for class_ in GATED_CLASSES:
        arm = H6_MAP[class_]
        source = arms[arm].source(class_) if arm in arms else None
        if source is None:
            continue
        mine = [q for q in source.questions() if q["class"] == class_]
        oracle_all += mine
        mark = " (retrieval only)" if source.mode == RETRIEVAL_ONLY else ""
        cells = []
        for name in names:
            found = [q for q in _router_questions(routers[name]) if q["class"] == class_]
            router_all[name] += found
            cells.append(_text(_recall(found)))
        lines.append(row_text([class_, f"{arm}{mark}", _text(_recall(mine)), *cells]))
    lines.append(row_text([POOLED, "-", _text(_recall(oracle_all)),
                       *(_text(_recall(router_all[n])) for n in names)]))
    return lines


def _router_questions(runs: ArmRuns) -> list[Mapping]:
    return [q for f in runs.full_files() for q in f.questions()]


def _text(recall: float | None) -> str:
    return "n/a" if recall is None else f"{recall:.3f}"
