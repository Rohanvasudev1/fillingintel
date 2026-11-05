"""README block 1: the ``test`` headline table (Step 14 ticket 03).

One row per arm and router: recall@10 per answerable class, pooled recall@10, the
pooled paired difference against ``vector``, pooled faithfulness, cost per question
and p95 total latency.  A row pools each class from the file ``results.md`` reads
for that class, so graph_global's pooled recall joins its full global run with its
retrieval-only run of the other classes.  Pooled intervals bootstrap the questions
with the run's seed; the paired difference uses ``eval.compare``'s generator key,
so it equals ``results.md``'s pooled row wherever that row exists.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

from eval.bootstrap import interval_rng, is_directional, mean_interval, percentile
from eval.gate_scores import GATED_CLASSES, POOLED
from eval.results_inputs import CONTROL_ARM
from eval.results_render import (
    NO_GOLD_JUDGED,
    RETRIEVAL_ONLY_TEXT,
    judged_labels,
    row_text,
    value,
)
from eval.results_sources import RETRIEVAL_ONLY, ArmRuns, ResultsInputError, RunFile, Split

RECALL = "recall@10"
FAITHFULNESS = "faithfulness"
DASH = "-"


@dataclass(frozen=True)
class _Question:
    record: Mapping[str, object]
    retrieval_only: bool

    @property
    def id(self) -> str:
        return str(self.record["id"])


def _source(runs: ArmRuns, class_: str) -> RunFile:
    source = runs.source(class_)
    if source is None:
        raise ResultsInputError(f"{runs.arm} has no run holding {class_}")
    return source


def _pooled(runs: ArmRuns) -> list[_Question]:
    """The answerable questions of each class from the file that class's row reads, in
    file order then each file's own question order (the order ``eval.compare`` pairs in)."""
    sources = {class_: _source(runs, class_) for class_ in GATED_CLASSES}
    return [_Question(q, source.mode == RETRIEVAL_ONLY) for source in runs.files
            for q in source.questions() if sources.get(q["class"]) is source]


def _seed(runs: ArmRuns) -> int:
    return int(runs.files[0].header["seed"])


def _mean_text(values: Sequence[float], seed: int, *key: str) -> str:
    return value(fmean(values), mean_interval(values, interval_rng(seed, *key)))


def _paired_text(control: list[_Question], other: list[_Question], seed: int) -> str:
    """``eval.compare``'s pooled recall@10 difference: control order, same generator."""
    theirs = {q.id: q.record for q in other}
    shared = [q.record for q in control if q.id in theirs]
    if not shared:
        return "n/a"
    diffs = [theirs[str(q["id"])]["metrics"][RECALL] - q["metrics"][RECALL] for q in shared]
    question_set = str(shared[0]["question_set"])
    key = ("paired", question_set, POOLED, f"metrics.{RECALL}")
    return value(fmean(diffs), mean_interval(diffs, interval_rng(seed, *key)))


def _answered(questions: list[_Question]) -> tuple[list[Mapping], int]:
    full = [q.record for q in questions if not q.retrieval_only]
    return full, len(questions) - len(full)


def _faithfulness(questions: list[_Question], seed: int, arm: str) -> str:
    full, skipped = _answered(questions)
    if skipped:
        return RETRIEVAL_ONLY_TEXT
    scores = [q["judged"]["metrics"][FAITHFULNESS]["mean"] for q in full
              if q.get("judged") and FAITHFULNESS in q["judged"]["metrics"]]
    return _mean_text(scores, seed, "readme", arm, FAITHFULNESS) if scores else "n/a"


def _cost_and_p95(questions: list[_Question]) -> tuple[str, str]:
    full, skipped = _answered(questions)
    if skipped:
        return RETRIEVAL_ONLY_TEXT, RETRIEVAL_ONLY_TEXT
    cost = fmean(float(q["cost_usd"]["total"]) for q in full)
    totals = [q.get("total_ms") for q in full]
    if any(t is None for t in totals):
        return f"{cost:.4f}", "not recorded"
    replayed = sum(1 for q in full if q.get("replayed"))
    note = f" ({replayed} replayed)" if replayed else ""
    return f"{cost:.4f}", f"{percentile(totals, 0.95):,.0f}{note}"


def _class_ns(control: ArmRuns) -> dict[str, int]:
    return {c: int(_source(control, c).cell(c)["n"]) for c in GATED_CLASSES}


def _class_cell(runs: ArmRuns, class_: str, n: int) -> str:
    cell = _source(runs, class_).cell(class_)
    text = f"{cell['metrics'][RECALL]:.3f}"
    return text if cell["n"] == n else f"{text} (n {cell['n']})"


def _head(ns: Mapping[str, int], label: str) -> list[str]:
    classes = [f"{c} (n {n}{', directional' if is_directional(n) else ''})"
               for c, n in ns.items()]
    head = ["row", *classes, f"pooled {RECALL} [95%]", "vs vector [95%]",
            f"faithfulness ({label})", "cost / question (USD)", "p95 total ms"]
    return [row_text(head), row_text(["---"] * len(head))]


def _label_line(split: Split, pooled_n: int) -> str:
    files = [f for runs in split.values() for f in runs.files]
    commits = ", ".join(f"`{c}`" for c in sorted({str(f.header["commit"]) for f in files}))
    ids = {str(q["id"]) for q in split[CONTROL_ARM].files[0].questions()}
    return (f"`test` split: {len(ids)} agent-drafted questions ({pooled_n} answerable), "
            f"scored once with `--final` at {commits}. Recall is gold chunks in the top 10. "
            "Judged columns come from gpt-6-luna and are uncalibrated. A class under 10 "
            "questions is directional. Intervals are fixed-seed bootstrap 95%; a paired "
            "difference counts only when its interval excludes 0.")


def _no_gold_line(split: Split) -> str:
    control = split[CONTROL_ARM]
    ns = {c: _source(control, c).cell(c)["n"] for c in NO_GOLD_JUDGED}
    means = {(arm, c): _source(runs, c).cell(c)["judged"]["metrics"][m]["mean"]
             for arm, runs in split.items() for c, m in NO_GOLD_JUDGED.items()}
    head = f"Decline ({ns['decline']}) and unanswerable ({ns['unanswerable']}) questions"
    if all(v == 1.0 for v in means.values()):
        return (f"{head}, judged (uncalibrated), directional: every row declined the advice "
                "questions and said the filings lack the answer to the unanswerable ones.")
    parts = [f"{arm} {means[(arm, 'decline')]:.3f} / {means[(arm, 'unanswerable')]:.3f}"
             for arm in split]
    return f"{head}, share correct (judged, uncalibrated), directional: {'; '.join(parts)}."


def headline_block(split: Split) -> list[str]:
    """*split* holds the ``test`` arms, then the router rows, in table order."""
    control = split[CONTROL_ARM]
    ns, seed = _class_ns(control), _seed(control)
    pooled = {arm: _pooled(runs) for arm, runs in split.items()}
    lines = [_label_line(split, len(pooled[CONTROL_ARM])), "",
             *_head(ns, judged_labels(split))]
    for arm, runs in split.items():
        questions = pooled[arm]
        recall = _mean_text([q.record["metrics"][RECALL] for q in questions], seed,
                            "readme", arm, RECALL)
        paired = DASH if arm == CONTROL_ARM else _paired_text(pooled[CONTROL_ARM], questions,
                                                              seed)
        lines.append(row_text([arm, *(_class_cell(runs, c, n) for c, n in ns.items()), recall,
                           paired, _faithfulness(questions, seed, arm),
                           *_cost_and_p95(questions)]))
    lines += ["", *_partial_notes(pooled), _no_gold_line(split)]
    return lines


def _partial_notes(pooled: Mapping[str, list[_Question]]) -> list[str]:
    notes = []
    for arm, questions in pooled.items():
        skipped = sum(q.retrieval_only for q in questions)
        if skipped:
            notes += [f"{arm} answered only some classes on `test`: {skipped} of "
                      f"{len(questions)} answerable questions were scored retrieval only, so "
                      "its pooled faithfulness, cost and latency are not given.", ""]
    return notes
