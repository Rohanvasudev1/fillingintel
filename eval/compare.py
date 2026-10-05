"""The score change between two results files of the same setup (Step 5).

Usage::

    uv run python -m eval.compare benchmarks/runs/<before>.json benchmarks/runs/<after>.json

Every numeric value in every (question set, class) cell is compared as
``after - before``.  Interval bounds are left out: they describe one run's
uncertainty, not its score.  Cells are matched by question set and class, not
arm, so the same command compares a repeat with its baseline or one arm with
another.  Runs on a different eval set, split or answer prompt are refused,
because their scores answer different questions.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

EXIT_USAGE = 2
# Header fields two runs must share for their scores to be comparable.
MUST_MATCH = ("split", "answer_prompt")
_INTERVAL_KEYS = frozenset({"interval", "intervals", "share_of_wrong_interval"})


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


def _mapping(node: object, where: str) -> Mapping[str, object]:
    if not isinstance(node, Mapping):
        raise CompareError(f"malformed cells: {where} is not an object")
    return node


def _cells(document: Mapping[str, object]) -> dict[tuple[str, str], dict[str, float]]:
    """``{(question set, class): {metric path: value}}``; a results file holds one arm."""
    cells = {}
    for set_name, arms in _mapping(document.get("cells"), "cells").items():
        for arm, classes in _mapping(arms, set_name).items():
            for cls, cell in _mapping(classes, f"{set_name}.{arm}").items():
                cells[(set_name, cls)] = dict(_leaves(_mapping(cell, f"{set_name}.{arm}.{cls}")))
    return cells


def compare(before: Mapping[str, object], after: Mapping[str, object]) -> list[Change]:
    """One ``Change`` per numeric cell value in either run, in *before*'s cell order."""
    headers = before.get("header"), after.get("header")
    if not all(isinstance(h, Mapping) for h in headers):
        raise CompareError("a results file has no header")
    _check_setup(*headers)
    before_cells, after_cells = _cells(before), _cells(after)
    changes = []
    for key in dict.fromkeys([*before_cells, *after_cells]):
        old, new = before_cells.get(key, {}), after_cells.get(key, {})
        for metric in dict.fromkeys([*old, *new]):
            changes.append(Change(*key, metric, old.get(metric), new.get(metric)))
    return changes


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
    except CompareError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    for label, path, document in (("before", args.before, before), ("after", args.after, after)):
        header = document["header"]
        print(f"{label}: {path.name} (arm {header.get('arm')}, commit {header.get('commit')})")
    print("set\tclass\tmetric\tbefore\tafter\tchange")
    for c in changes:
        print("\t".join([c.question_set, c.class_, c.metric,
                         _fmt(c.before), _fmt(c.after), _fmt(c.delta)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
