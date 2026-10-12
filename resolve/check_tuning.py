"""The Step 9b tuning check: does a newer run merge any tuning pair labelled not same?

Usage (offline; no API key or database)::

    uv run python -m resolve.check_tuning --before 2 --after 3

The command rebuilds the alias map each run produced, by replaying the merges
its run report lists from the merge log (no merge has been split), and
compares the two on the tuning labels: the pairs merged only by the newer run,
the pairs it no longer merges, and how many pairs each map gets right. The
report goes to ``benchmarks/resolution/tuning-check.json``, replacing an
earlier one; the freeze for scoring round 2 reads it. Every figure is
"agent-labelled (gpt-6-luna)". It is a report, not a tuning loop: nothing is
adjusted on it.

Exit codes: 0 no newly merged pair is labelled not same; 2 bad arguments or a
malformed file; 3 could not run (a run report, the merge log or the tuning
labels missing, or a run naming a merge the log lacks); 4 a newly merged pair
is labelled not same, so the step stops for the user before freezing.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from ingest.atomic_json import write_json_atomic
from ingest.provenance import git_state
from resolve.label_report import now
from resolve.labels import (
    CHECK_REPORT_NAME,
    LabelFileError,
    labels_path,
    read_json,
    read_labels,
)
from resolve.log import LogError, MergeEvent, read_log, replay
from resolve.paths import MERGE_LOG_NAME, OUTPUT_DIR
from resolve.scoring import LABEL_NOTE
from resolve.tuning import MergeChanges, PairKeys, TunedLabel, merge_changes

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_CHECK_FAILED = 4


class _Stop(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m resolve.check_tuning",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--before", type=int, required=True, help="the older run's number")
    parser.add_argument("--after", type=int, required=True, help="the newer run's number")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc


def main(argv: Sequence[str] | None = None, *, out_dir: Path = OUTPUT_DIR) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    try:
        args = _parse_args(argv)
        return _run(args.before, args.after, out_dir)
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


def _run(before: int, after: int, out_dir: Path) -> int:
    try:
        log = [e for e in read_log(out_dir / MERGE_LOG_NAME) if isinstance(e, MergeEvent)]
        labels = tuple(TunedLabel(lb.key_a, lb.key_b, lb.same)
                       for lb in read_labels(labels_path(out_dir, "tuning")))
        runs = {n: _read_run(out_dir, n) for n in (before, after)}
    except (LabelFileError, LogError, ValueError) as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the inputs: {exc}") from exc
    if not labels:
        raise _Stop(EXIT_RUN_ERROR, "no tuning labels; run python -m resolve.label --set tuning")
    changes = merge_changes(_aliases(log, runs[before], before),
                            _aliases(log, runs[after], after), labels)
    path = out_dir / CHECK_REPORT_NAME
    try:
        write_json_atomic(path, _report(changes, labels, runs, before, after), indent=2)
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not write {CHECK_REPORT_NAME}: {exc}") from exc
    print(f"run {after} against run {before} on {changes.labels} tuning pairs: "
          f"{len(changes.newly_merged)} newly merged, {len(changes.no_longer_merged)} no longer "
          f"merged; agreed with the labels {changes.agreed_before} -> {changes.agreed_after} "
          f"({LABEL_NOTE}); wrote {CHECK_REPORT_NAME}")
    if changes.newly_merged_not_same:
        raise _Stop(EXIT_CHECK_FAILED, f"{len(changes.newly_merged_not_same)} newly merged "
                                       "tuning pairs are labelled not same; stop for the user "
                                       "before freezing")
    return EXIT_OK


def _read_run(out_dir: Path, n: int) -> Mapping[str, object]:
    path = out_dir / f"run{n}.json"
    if not path.exists():
        raise _Stop(EXIT_RUN_ERROR, f"no {path.name}")
    return read_json(path)


def _aliases(log: Sequence[MergeEvent], run: Mapping[str, object], n: int) -> dict[str, str]:
    """The alias map run *n* produced: the logged merges its report lists, replayed."""
    listed = run.get("merges")
    if not isinstance(listed, list):
        raise _Stop(EXIT_USAGE, f"run{n}.json lists no merges")
    ids = {m.get("merge_id") for m in listed if isinstance(m, dict)}
    by_id = {m.merge_id: m for m in log}
    if missing := sorted(str(i) for i in ids - by_id.keys()):
        raise _Stop(EXIT_RUN_ERROR, f"run{n}.json names {len(missing)} merges the log lacks, "
                                    f"such as {missing[0]}")
    return replay(by_id[i] for i in sorted(ids, key=str))


def _pairs(keys: Sequence[PairKeys], same: Mapping[PairKeys, bool]) -> list[dict[str, object]]:
    return [{"key_a": a, "key_b": b, "same": same[(a, b)]} for a, b in keys]


def _report(changes: MergeChanges, labels: Sequence[TunedLabel],
            runs: Mapping[int, Mapping[str, object]], before: int,
            after: int) -> dict[str, object]:
    same = {lb.keys: lb.same for lb in labels}

    def run(n: int) -> dict[str, object]:
        return {"run": f"run{n}.json",
                "config_hash": (runs[n].get("provenance") or {}).get("config_hash")}

    return {
        "provenance": {"note": LABEL_NOTE, "commit": git_state(), "created_at": now(),
                       "before": run(before), "after": run(after)},
        "labels": changes.labels,
        "agreed_before": changes.agreed_before, "agreed_after": changes.agreed_after,
        "newly_merged": _pairs(changes.newly_merged, same),
        "no_longer_merged": _pairs(changes.no_longer_merged, same),
        "newly_merged_not_same": _pairs(changes.newly_merged_not_same, same),
    }


if __name__ == "__main__":
    sys.exit(main())
