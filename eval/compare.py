"""The score change between two results files of the same setup (Steps 5 and 11).

Usage::

    uv run python -m eval.compare benchmarks/runs/<before>.json benchmarks/runs/<after>.json

Two reports.  First, every numeric value in every (question set, class) cell
is compared as ``after - before``; interval bounds are left out, since they
describe one run's uncertainty, not its score.  Second, the paired comparison
the research plan asks for: on the questions both runs hold, each question's
``after - before`` in recall and precision at 5 and 10, wrong evidence and each
judged mean, per class and pooled over the answerable classes, with a
fixed-seed percentile bootstrap 95% interval of the mean difference.  A
difference counts only when its interval excludes 0.  Questions in only one
run, and shared questions only one run scored on a metric, are counted, never
silently dropped.

A run limited with ``eval.run --classes`` pairs with a full run on the
questions both hold; the full run's other questions are listed as only in it.

Cells and questions are matched by question set, not arm, so the same command
compares a repeat with its baseline or one arm with another.  Runs on a
different eval set, split or answer prompt are refused, because their scores
answer different questions.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import get_args

from eval.bootstrap import (
    BOOTSTRAP_RESAMPLES,
    CONFIDENCE,
    interval_rng,
    is_directional,
    mean_interval,
)
from eval.gate_scores import POOLED
from eval.schema import NO_GOLD_CLASSES, Class

EXIT_USAGE = 2
# Header fields two runs must share for their scores to be comparable.
MUST_MATCH = ("split", "answer_prompt")
_INTERVAL_KEYS = frozenset({"interval", "intervals", "share_of_wrong_interval"})
WRONG_EVIDENCE = "wrong_evidence.rate"  # the cell path of the wrong-evidence rate


class CompareError(ValueError):
    """The two files cannot be compared."""


@dataclass(frozen=True)
class Change:
    question_set: str
    class_: str
    metric: str  # dotted path inside the cell, e.g. "metrics.recall@5"
    before: float | None
    after: float | None

    @property
    def delta(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before


def _question_sets(header: Mapping[str, object]) -> list[tuple[object, object]]:
    sets = header.get("question_sets")
    if not isinstance(sets, list):
        return []
    return [(s.get("name"), s.get("sha256")) for s in sets if isinstance(s, dict)]


def _check_setup(before: Mapping[str, object], after: Mapping[str, object]) -> None:
    differ = [f for f in MUST_MATCH if before.get(f) != after.get(f)]
    if _question_sets(before) != _question_sets(after):
        differ.append("question_sets")
    if differ:
        raise CompareError(f"the runs differ in {', '.join(differ)}; "
                           "only runs of the same setup can be compared")


def _leaves(node: object, prefix: str = "") -> Iterator[tuple[str, float]]:
    """Numeric leaves of *node* by dotted path, skipping interval bounds."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key not in _INTERVAL_KEYS:
                yield from _leaves(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(node, int | float) and not isinstance(node, bool):
        yield prefix, float(node)


def _mapping(node: object, where: str, kind: str = "cells") -> Mapping[str, object]:
    if not isinstance(node, Mapping):
        raise CompareError(f"malformed {kind}: {where} is not an object")
    return node


def _cells(document: Mapping[str, object]) -> dict[tuple[str, str], dict[str, float]]:
    """``{(question set, class): {metric path: value}}``; a results file holds one arm."""
    cells = {}
    for set_name, arms in _mapping(document.get("cells"), "cells").items():
        for arm, classes in _mapping(arms, set_name).items():
            for cls, cell in _mapping(classes, f"{set_name}.{arm}").items():
                cells[(set_name, cls)] = dict(_leaves(_mapping(cell, f"{set_name}.{arm}.{cls}")))
    return cells


def _headers(
    before: Mapping[str, object], after: Mapping[str, object]
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Both headers, once the runs are known to share a setup."""
    headers = before.get("header"), after.get("header")
    if not (isinstance(headers[0], Mapping) and isinstance(headers[1], Mapping)):
        raise CompareError("a results file has no header")
    _check_setup(*headers)
    return headers[0], headers[1]


def compare(before: Mapping[str, object], after: Mapping[str, object]) -> list[Change]:
    """One ``Change`` per numeric cell value in either run, in *before*'s cell order."""
    _headers(before, after)
    before_cells, after_cells = _cells(before), _cells(after)
    changes = []
    for key in dict.fromkeys([*before_cells, *after_cells]):
        old, new = before_cells.get(key, {}), after_cells.get(key, {})
        for metric in dict.fromkeys([*old, *new]):
            changes.append(Change(*key, metric, old.get(metric), new.get(metric)))
    return changes


QuestionKey = tuple[str, str]  # (question set, question id)
_CLASS_ORDER = get_args(Class)


@dataclass(frozen=True)
class PairedDifference:
    """The mean of ``after - before`` over the questions both runs scored on *metric*."""

    question_set: str
    class_: str  # a schema class, or ``POOLED`` (the answerable classes together)
    metric: str  # its path in the cells, e.g. "judged.metrics.faithfulness.mean"
    n: int
    mean: float
    low: float
    high: float
    unpaired: int  # questions of the class both runs hold that only one run scored on *metric*
    label: str | None  # the judged block's label ("uncalibrated"); None for retrieval

    @property
    def directional(self) -> bool:
        return is_directional(self.n)

    @property
    def counts(self) -> bool:
        """Whether the interval excludes 0, the research plan's test for a difference."""
        return self.low > 0 or self.high < 0


@dataclass(frozen=True)
class Paired:
    seed: int
    differences: tuple[PairedDifference, ...]
    only_before: tuple[QuestionKey, ...]
    only_after: tuple[QuestionKey, ...]


@dataclass(frozen=True)
class _Question:
    class_: str
    values: Mapping[str, float]  # metric path -> value; absent when not scored
    labels: Mapping[str, str]  # metric path -> label, for judged metrics


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CompareError(f"malformed question record: {where} is not a number")
    return float(value)


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        raise CompareError(f"malformed question record: {field} is missing in {dict(record)!r}")
    return value


def _question(record: Mapping[str, object], qid: str) -> _Question:
    """The retrieval metrics, wrong-evidence flag and judged means one question was scored on.

    Each value is named by its path in the cells, so the two reports line up.
    """
    def mapping(node: object, where: str) -> Mapping[str, object]:
        return _mapping(node, f"question {qid} {where}", kind="question record")

    metrics = mapping(record.get("metrics") or {}, "metrics")
    values = {f"metrics.{m}": _number(v, f"question {qid} {m}") for m, v in metrics.items()}
    hits = record.get("wrong_evidence")
    if hits is not None:
        if not isinstance(hits, list):
            raise CompareError(f"malformed question record: question {qid} wrong_evidence")
        values[WRONG_EVIDENCE] = float(bool(hits))
    judged = mapping(record.get("judged") or {}, "judged")
    label = judged.get("label")
    labels = {}
    for metric, summary in mapping(judged.get("metrics") or {}, "judged metrics").items():
        mean = mapping(summary, f"judged {metric}").get("mean")
        if mean is not None:
            path = f"judged.metrics.{metric}.mean"
            values[path] = _number(mean, f"question {qid} judged {metric}")
            labels[path] = str(label)
    return _Question(_text(record, "class"), values, labels)


def _questions(document: Mapping[str, object]) -> dict[QuestionKey, _Question]:
    records = document.get("questions")
    if not isinstance(records, list):
        raise CompareError("malformed questions: the results file has no question list")
    questions: dict[QuestionKey, _Question] = {}
    for record in records:
        record = _mapping(record, "a question record", kind="question record")
        key = (_text(record, "question_set"), _text(record, "id"))
        if key in questions:
            raise CompareError(f"malformed questions: question {key[1]} appears twice")
        questions[key] = _question(record, key[1])
    return questions


def _seed(before_header: Mapping[str, object]) -> int:
    seed = before_header.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise CompareError("the before run's header has no integer seed")
    return seed


def _difference(
    seed: int, set_name: str, class_: str, metric: str, pairs: list[tuple[_Question, _Question]]
) -> PairedDifference | None:
    """*metric*'s paired difference over *pairs*; None when no pair holds it on both sides."""
    held = [(metric in b.values, metric in a.values) for b, a in pairs]
    diffs = [a.values[metric] - b.values[metric]
             for (b, a), both in zip(pairs, held, strict=True) if all(both)]
    if not diffs:
        return None
    interval = mean_interval(diffs, interval_rng(seed, "paired", set_name, class_, metric))
    if interval is None:  # mean_interval returns None only for an empty list
        raise CompareError(f"no interval for {set_name} {class_} {metric}")
    label = next((b.labels[metric] for b, _ in pairs if metric in b.labels), None)
    return PairedDifference(
        set_name, class_, metric, len(diffs), fmean(diffs),
        float(interval["low"]), float(interval["high"]),
        unpaired=sum(1 for flags in held if any(flags) and not all(flags)),
        label=label)


def _group(
    seed: int, set_name: str, class_: str, pairs: list[tuple[_Question, _Question]]
) -> list[PairedDifference]:
    metrics = dict.fromkeys(m for b, a in pairs for m in (*b.values, *a.values))
    found = (_difference(seed, set_name, class_, m, pairs) for m in metrics)
    return [d for d in found if d is not None]


def _class_rank(class_: str) -> int:
    return _CLASS_ORDER.index(class_) if class_ in _CLASS_ORDER else len(_CLASS_ORDER)


def compare_paired(before: Mapping[str, object], after: Mapping[str, object]) -> Paired:
    """Paired differences per (question set, class) and pooled, on the questions both hold.

    Classes come in schema order.  ``POOLED`` holds the answerable classes only; decline
    and unanswerable are reported in their own rows (research plan).  Each interval's
    generator is seeded by *before*'s seed and the set, class and metric.
    """
    before_header, _ = _headers(before, after)
    seed = _seed(before_header)
    old, new = _questions(before), _questions(after)
    shared = [key for key in old if key in new]
    differences: list[PairedDifference] = []
    for set_name in dict.fromkeys(s for s, _ in shared):
        in_set = [(old[k], new[k]) for k in shared if k[0] == set_name]
        for class_ in sorted(dict.fromkeys(b.class_ for b, _ in in_set), key=_class_rank):
            in_class = [(b, a) for b, a in in_set if b.class_ == class_]
            differences += _group(seed, set_name, class_, in_class)
        answerable = [(b, a) for b, a in in_set if b.class_ not in NO_GOLD_CLASSES]
        differences += _group(seed, set_name, POOLED, answerable)
    return Paired(
        seed=seed,
        differences=tuple(differences),
        only_before=tuple(k for k in old if k not in new),
        only_after=tuple(k for k in new if k not in old),
    )


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.4g}"


def _load(path: Path) -> Mapping[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompareError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise CompareError(f"{path} is not a results file")
    return document


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Exit codes: 0 ok; 2 the files cannot be compared."""
    parser = argparse.ArgumentParser(description="Score change between two results files.")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args(argv)
    try:
        before, after = _load(args.before), _load(args.after)
        changes = compare(before, after)
        paired = compare_paired(before, after)
    except CompareError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    for label, path, document in (("before", args.before, before), ("after", args.after, after)):
        header = document["header"]
        classes = ",".join(header.get("classes") or ()) or "all"
        print(f"{label}: {path.name} (arm {header.get('arm')}, commit {header.get('commit')}, "
              f"classes {classes})")
    print("set\tclass\tmetric\tbefore\tafter\tchange")
    for c in changes:
        print("\t".join([c.question_set, c.class_, c.metric,
                         _fmt(c.before), _fmt(c.after), _fmt(c.delta)]))
    print()
    _print_paired(paired)
    return 0


def _unpaired(side: str, keys: tuple[QuestionKey, ...]) -> str:
    listed = f" ({', '.join(f'{s}:{q}' for s, q in keys)})" if keys else ""
    return f"only in {side}: {len(keys)}{listed}"


def _print_paired(paired: Paired) -> None:
    print(f"paired differences (after - before) on shared questions: mean and percentile "
          f"bootstrap {CONFIDENCE:.0%} interval over {BOOTSTRAP_RESAMPLES} resamples, "
          f"seed {paired.seed}; counts = the interval excludes 0; directional = n under 10; "
          f"unpaired = shared questions only one run scored on the metric; pooled = the "
          f"answerable classes")
    print("set\tclass\tmetric\tn\tmean\tlow\thigh\tnote\tunpaired\tcounts")
    for d in paired.differences:
        note = ",".join(n for n in ("directional" if d.directional else None, d.label) if n)
        print("\t".join([d.question_set, d.class_, d.metric, str(d.n), _fmt(d.mean),
                         _fmt(d.low), _fmt(d.high), note or "-", str(d.unpaired),
                         "yes" if d.counts else "no"]))
    print(_unpaired("before", paired.only_before))
    print(_unpaired("after", paired.only_after))


if __name__ == "__main__":
    sys.exit(main())
