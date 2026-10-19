"""Judge calibration in ``benchmarks/results.md`` (Step 13 ticket 04).

The calibration report (``eval.calibration.report``) sets each judged column's
label: a column whose statistic passed is labelled with the report's label
("calibrated, n = 30, claims split by an agent"); one that failed leaves the
per-split tables and the paired differences, as RESEARCH-PLAN's rule says.
Decline and not-found correctness have no pass rule, so their column says how
often the judge agreed with the user.  Before a report exists every judged
column keeps the run files' own label ("uncalibrated").  The corruption test's
catch rates are printed beside the statistics, labelled as not calibration.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from eval.results_sources import InputFile, read_json

JUDGED = ("faithfulness", "answer_relevancy", "citation_support")
NO_GOLD = (("decline_correct", "decline"), ("not_found_correct", "not-found"))
DEFERRED = ("Calibration: deferred (user decision, 2026-10-19; RESEARCH-PLAN amendment). The "
            "user has not scored the drawn answers, so every judged column is uncalibrated. "
            "The tools, draw and claims are committed (`python -m eval.calibrate`).")
KIND_NAMES = {"changed_number": "changed number", "swapped_period": "swapped period",
              "wrong_citation": "wrong citation", "added_sentence": "added sentence"}


@dataclass(frozen=True)
class Calibration:
    report: Mapping | None
    corruption: Mapping | None
    inputs: tuple[InputFile, ...]
    report_path: str
    corruption_path: str

    def column_labels(self, fallback: str) -> dict[str, str | None]:
        """Judged metric -> its column label, or None when it failed calibration."""
        if self.report is None:
            return dict.fromkeys(JUDGED, fallback)
        rows = self.report["metrics"]
        return {m: rows[m]["label"] if rows[m]["passed"] else None for m in JUDGED}

    def no_gold_label(self, fallback: str) -> str:
        if self.report is None:
            return fallback
        rows = self.report["metrics"]
        agreed = ", ".join(f"{name} {round(rows[m]['value'] * rows[m]['n'])} of {rows[m]['n']}"
                           for m, name in NO_GOLD if rows[m]["n"])
        return f"{fallback}; {agreed} agree with the user"

    def failed_notes(self) -> list[str]:
        if self.report is None:
            return []
        rows = self.report["metrics"]
        return [f"{m.replace('_', ' ')} failed calibration ({rows[m]['statistic']} "
                f"{_number(rows[m]['value'])}, below {self.report['pass_at']}), so it is left "
                "out of these tables and the paired differences."
                for m in JUDGED if not rows[m]["passed"]]


def read_calibration(report_path: str, corruption_path: str, root: Path) -> Calibration:
    """The committed report and corruption run, each None while it does not exist."""
    found, inputs = [], []
    for path in (report_path, corruption_path):
        if (root / path).exists():
            document, info = read_json(path, root)
            found.append(document)
            inputs.append(info)
        else:
            found.append(None)
    return Calibration(found[0], found[1], tuple(inputs), report_path, corruption_path)


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _interval(row: Mapping) -> str:
    interval = row.get("interval_95")
    text = _number(row["value"])
    return f"{text} [{interval[0]:.3f}, {interval[1]:.3f}]" if interval else text


def _commit(calibration: Calibration, path: str) -> str:
    info = next((i for i in calibration.inputs if i.path == path), None)
    return f"`{path}` at `{info.commit}`" if info and info.commit else f"`{path}`"


def _report_lines(calibration: Calibration) -> list[str]:
    report = calibration.report
    if report is None:
        return [DEFERRED, ""]
    rows = report["metrics"]
    lines = [f"Calibration against the user's own scores of {report['scored']} of "
             f"{report['drawn']} drawn dev answers (each judged column on its own n of "
             "answerable answers; claims split by an agent), "
             f"{_commit(calibration, calibration.report_path)}. Pass: rho or kappa of at "
             f"least {report['pass_at']} on the point estimate.", "",
             "| metric | statistic | n | value [95%] | MAE | judge - user | judge vs itself | "
             "passed |", "|---|---|---|---|---|---|---|---|"]
    for metric, row in rows.items():
        n = row.get("n", row.get("n_sentences"))
        passed = {True: "yes", False: "no", None: "no rule"}[row["passed"]]
        lines.append(f"| {metric.replace('_', ' ')} | {row['statistic']} | {n} | "
                     f"{_interval(row)} | {_number(row.get('mae'))} | "
                     f"{_number(row.get('signed_bias'))} | "
                     f"{_number(row.get('judge_self_agreement'))} | {passed} |")
    pace = report["pace"]
    lines += ["", f"Pace: median {pace['median_seconds']:.0f} s per answer, "
                  f"{pace['under_15_seconds']} under 15 s, {pace['looked_again']} looked at "
                  "again."]
    if report.get("rescore") is None:
        lines.append("The re-score of 10 answers for intra-rater agreement has not been done.")
    return [*lines, ""]


def _corruption_lines(calibration: Calibration) -> list[str]:
    run = calibration.corruption
    if run is None:
        return ["Corruption test: not run yet (`python -m eval.corrupt`).", ""]
    source = _commit(calibration, calibration.corruption_path)
    lines = [f"Corruption test, not calibration: {source}. "
             "Four known errors planted in 30 dev answers outside the calibration draw. A "
             "faithfulness catch is a lower mean score than the uncorrupted answer's; a "
             "citation-support catch is a not-supported majority on the changed sentence.", "",
             "| error | copies | skipped | faithfulness caught [Wilson 95%] | faithfulness "
             "before -> after | citation support caught [Wilson 95%] | already not supported |",
             "|---|---|---|---|---|---|---|"]
    for kind, row in run["catch_rates"].items():
        faith, cite = row["faithfulness"], row["citation_support"]
        lines.append(f"| {KIND_NAMES.get(kind, kind)} | {row['applied']} | {row['skipped']} | "
                     f"{_caught(faith)} | {_number(faith['original_mean'])} -> "
                     f"{_number(faith['corrupted_mean'])} | {_caught(cite)} | "
                     f"{cite['already_not_supported']} |")
    lines += ["", "Faithfulness reads the answer without its citations, so a wrong citation "
                  "cannot lower it: that row is a control."]
    return [*lines, ""]


def _caught(row: Mapping) -> str:
    if not row["scored"]:
        return "n/a"
    low, high = (max(0.0, b) for b in row["wilson_95"])
    return f"{row['caught']} of {row['scored']} ({row['rate']:.3f} [{low:.3f}, {high:.3f}])"


def calibration_block(calibration: Calibration) -> list[str]:
    return [*_report_lines(calibration), *_corruption_lines(calibration)]


def dropped_metrics(labels: Mapping[str, str | None]) -> Sequence[str]:
    return [m for m, label in labels.items() if label is None]
