"""The Step 10 alias map check: after the replay, only MetricValue entries may move.

Usage (offline; no API key or database)::

    git show <commit>:benchmarks/resolution/alias_map.json > before.json
    uv run python -m resolve.check_aliases --before before.json

The replay changed the metric-value key (it gained the unit class), so
resolution was rerun and its MetricValue entries changed. Every other label's
entries must be what they were, so scoring round 2 still describes the map
the graph loads. The command compares *before* with the alias map on disk,
skipping MetricValue keys, and lists every key added, removed or mapped to
another canonical key. Each key's label comes from the merge log, which is
append-only and so names every key either map holds.

Exit codes: 0 no change outside MetricValue; 2 bad arguments or a malformed
file; 3 could not run (a file missing or unreadable, or a key no merge in the
log names); 4 a change, listed, so the step stops for the user.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from resolve.log import LogError, MergeEvent, read_log
from resolve.nodes import METRIC_LABEL
from resolve.paths import ALIAS_MAP_NAME, MERGE_LOG_NAME, OUTPUT_DIR, read_alias_map

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_CHECK_FAILED = 4


@dataclass(frozen=True, slots=True)
class AliasChange:
    """One key whose canonical key differs; None where the map has no entry."""

    label: str
    key: str
    before: str | None
    after: str | None


def alias_changes(before: Mapping[str, str], after: Mapping[str, str],
                  labels: Mapping[str, str]) -> tuple[AliasChange, ...]:
    """Every key outside MetricValue whose entry differs between the maps, by key.

    ValueError when a key of either map has no label in *labels*.
    """
    keys = sorted(before.keys() | after.keys())
    if unknown := [k for k in keys if k not in labels]:
        raise ValueError(f"no merge names {unknown[0]} ({len(unknown)} keys unknown)")
    return tuple(AliasChange(labels[k], k, before.get(k), after.get(k)) for k in keys
                 if labels[k] != METRIC_LABEL and before.get(k) != after.get(k))


class _Stop(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m resolve.check_aliases",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--before", type=Path, required=True,
                        help="the committed alias map from before the replay")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc


def _changes(before_path: Path, out_dir: Path) -> tuple[tuple[AliasChange, ...], int, int, int]:
    """The changes, the entry counts before and after, and the MetricValue entries after."""
    paths = (before_path, out_dir / ALIAS_MAP_NAME, out_dir / MERGE_LOG_NAME)
    if missing := [p for p in paths if not p.exists()]:
        raise _Stop(EXIT_RUN_ERROR, f"{missing[0]} does not exist")
    try:
        before, after = read_alias_map(paths[0]), read_alias_map(paths[1])
        log = read_log(paths[2])
    except (LogError, ValueError) as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the alias maps or merge log: {exc}") from exc
    labels = {k: e.label for e in log if isinstance(e, MergeEvent) for k in e.members}
    try:
        changes = alias_changes(before, after, labels)
    except ValueError as exc:  # a key the log lacks: the inputs do not belong together
        raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
    metric = sum(labels[k] == METRIC_LABEL for k in after)
    return changes, len(before), len(after), metric


def main(argv: Sequence[str] | None = None, *, out_dir: Path = OUTPUT_DIR) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    try:
        args = _parse_args(argv)
        changes, before, after, metric = _changes(args.before, out_dir)
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code
    for change in changes:
        print(f"{change.label}\t{change.key}\t{change.before} -> {change.after}")
    print(f"{len(changes)} changes outside {METRIC_LABEL}; {before} entries before, "
          f"{after} after ({metric} {METRIC_LABEL} entries after)")
    return EXIT_CHECK_FAILED if changes else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
