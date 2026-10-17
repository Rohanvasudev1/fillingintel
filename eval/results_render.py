"""The per-split tables of ``benchmarks/results.md``: answerable rows by class and arm,
decline and unanswerable rows, and paired differences against ``vector``.

Every value prints with its 95% interval.  A row read from a retrieval-only run
prints "retrieval only" where a judged, end-to-end latency or cost value would
be; a full run from before Step 13 prints "not recorded" for ``total_ms``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from eval.bootstrap import is_directional
from eval.compare import PairedDifference
from eval.gate_scores import GATED_CLASSES, POOLED
from eval.results_inputs import CONTROL_ARM
from eval.results_paired import difference_text, paired
from eval.results_sources import FULL, RETRIEVAL_ONLY, ResultsInputError, RunFile, Split

RETRIEVAL_ONLY_TEXT = "retrieval only"
NOT_RECORDED = "not recorded"
JUDGED = ("faithfulness", "answer_relevancy", "citation_support")
NO_GOLD_JUDGED = {"decline": "decline_correct", "unanswerable": "not_found_correct"}
RETRIEVAL = ("recall@5", "recall@10", "precision@10")
PAIRED_METRICS = (*(f"metrics.{m}" for m in RETRIEVAL), "wrong_evidence.rate",
                  *(f"judged.metrics.{m}.mean" for m in JUDGED))
PAIRED_NO_GOLD = {c: f"judged.metrics.{m}.mean" for c, m in NO_GOLD_JUDGED.items()}
METRIC_NAMES = {"wrong_evidence.rate": "wrong evidence",
                **{f"metrics.{m}": m for m in RETRIEVAL},
                **{f"judged.metrics.{m}.mean": m.replace("_", " ")
                   for m in (*JUDGED, *NO_GOLD_JUDGED.values())}}


def value(mean: float | None, interval: Mapping[str, float] | None, digits: int = 3) -> str:
    if mean is None:
        return "n/a"
    text = f"{mean:,.{digits}f}"
    if interval:
        text += f" [{interval['low']:,.{digits}f}, {interval['high']:,.{digits}f}]"
    return text


def _metric(cell: Mapping, name: str) -> str:
    return value(cell["metrics"].get(name), cell["intervals"].get(name))


def _wrong(cell: Mapping, mode: str) -> str:
    wrong = cell["wrong_evidence"]
    text = value(wrong["rate"], wrong["interval"])
    return f"{text} (ranks only)" if mode == RETRIEVAL_ONLY and wrong["rate"] is not None else text


def _judged(cell: Mapping, metric: str) -> str:
    found = cell["judged"]["metrics"].get(metric)
    return "n/a" if found is None else value(found["mean"], found["interval"])


def _total(cell: Mapping, mode: str) -> tuple[str, str]:
    if mode == RETRIEVAL_ONLY:
        return RETRIEVAL_ONLY_TEXT, RETRIEVAL_ONLY_TEXT
    total = cell.get("total_ms")
    if total is None:
        return NOT_RECORDED, NOT_RECORDED
    replayed = f" ({total['replayed']} replayed)" if total["replayed"] else ""
    return (value(total["p50"], total["intervals"]["p50"], 0) + replayed,
            value(total["p95"], total["intervals"]["p95"], 0))


def _cost(cell: Mapping, mode: str) -> str:
    if mode == RETRIEVAL_ONLY:
        return RETRIEVAL_ONLY_TEXT
    cost = cell["cost_usd"]
    return value(cost["per_query_total"], cost["intervals"]["per_query_total"], 4)


def _n(cell: Mapping) -> str:
    return f"{cell['n']}{' (directional)' if is_directional(cell['n']) else ''}"


def judged_labels(split: Split) -> str:
    """The judged label every full cell carries, e.g. "uncalibrated"."""
    labels = {f.cell(c)["judged"]["label"] for runs in split.values() for f in runs.files
              if f.mode == FULL for c in f.classes}
    if len(labels) != 1:
        raise ResultsInputError(f"judged cells carry different labels: {sorted(labels)}")
    return labels.pop()


def answerable_table(split: Split) -> list[str]:
    label = judged_labels(split)
    head = ["class", "arm", "n", *RETRIEVAL, "wrong evidence",
            *(f"{m.replace('_', ' ')} ({label})" for m in JUDGED),
            "p50 total ms", "p95 total ms", "cost / question (USD)"]
    lines = [_row(head), _row(["---"] * len(head))]
    for class_ in GATED_CLASSES:
        for arm, runs in split.items():
            source = runs.source(class_)
            if source is None:
                continue
            cell, mode = source.cell(class_), source.mode
            judged = ([RETRIEVAL_ONLY_TEXT] * len(JUDGED) if mode == RETRIEVAL_ONLY
                      else [_judged(cell, m) for m in JUDGED])
            lines.append(_row([class_, arm, _n(cell), *(_metric(cell, m) for m in RETRIEVAL),
                               _wrong(cell, mode), *judged, *_total(cell, mode),
                               _cost(cell, mode)]))
    return lines


def no_gold_table(split: Split) -> list[str]:
    label = judged_labels(split)
    head = ["class", "arm", "n", f"correct ({label})", "p50 total ms", "p95 total ms",
            "cost / question (USD)"]
    lines = [_row(head), _row(["---"] * len(head))]
    for class_, metric in NO_GOLD_JUDGED.items():
        for arm, runs in split.items():
            source = runs.source(class_)
            if source is None:
                continue
            cell = source.cell(class_)
            lines.append(_row([class_, arm, _n(cell), _judged(cell, metric),
                               *_total(cell, source.mode), _cost(cell, source.mode)]))
    return lines


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def paired_rows(split: Split) -> list[tuple[str, str, PairedDifference]]:
    """``(arm, file, difference)`` for every graph arm against vector, by arm then class:
    each class from the arm's source file for it, pooled from a file holding every
    answerable class (a full one first)."""
    control = split[CONTROL_ARM]
    rows = []
    for arm, runs in split.items():
        if arm == CONTROL_ARM:
            continue
        for class_ in (*GATED_CLASSES, POOLED, *NO_GOLD_JUDGED):  # a fixed order
            source = _pooled_source(runs.files) if class_ == POOLED else runs.source(class_)
            base = (_pooled_source(control.files) if class_ == POOLED
                    else control.source(class_))
            if source is None or base is None:
                continue
            metrics = PAIRED_NO_GOLD.get(class_, None)
            wanted = (metrics,) if metrics else PAIRED_METRICS
            found = {d.metric: d for d in paired(base, source).differences if d.class_ == class_}
            rows += [(arm, source.input.path, found[m]) for m in wanted if m in found]
    return rows


def _pooled_source(files: Sequence[RunFile]) -> RunFile | None:
    holding = [f for f in files if set(GATED_CLASSES) <= set(f.classes)]
    holding.sort(key=lambda f: f.mode != FULL)
    return holding[0] if holding else None


def paired_table(split: Split) -> list[str]:
    head = ["arm", "class", "metric", "n", "difference (arm - vector) [95%]", "counts",
            "note"]
    lines = [_row(head), _row(["---"] * len(head))]
    for arm, _, d in paired_rows(split):
        notes = [n for n in ("directional" if d.directional else None, d.label,
                             f"{d.unpaired} unpaired" if d.unpaired else None) if n]
        lines.append(_row([arm, d.class_, METRIC_NAMES.get(d.metric, d.metric), str(d.n),
                           difference_text(d),
                           "yes" if d.counts else "no", ", ".join(notes) or "-"]))
    return lines
