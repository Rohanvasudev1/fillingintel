"""Score the alias map against a labelled set (Step 9, ticket 04).

Usage (offline; no API key or database)::

    uv run python -m resolve.score
    uv run python -m resolve.score --set tuning

The command reads ``benchmarks/resolution/{set}-draw.json`` and
``{set}-labels.jsonl``, the merge log and alias map, and the latest run report,
and runs ``score()`` (resolve.scoring): weighted pairwise precision and recall
on the final groups, the adjudicator's accuracy on the labelled pairs it
answered, and the candidate rules' share of true matches, each with a
fixed-seed bootstrap 95% interval, plus raw counts per stratum. The report
goes to ``{set}-score{N}.json``. Every figure is "agent-labelled (gpt-6-luna)".

The adjudicator's answers come from the run report's ``adjudication`` section
(ticket 03): ``decisions``, a list of objects with ``key_a``, ``key_b`` and
``answer`` (``same``, ``related``, ``distinct``, ``unsure`` or null when no
usable reply came back), and ``model``, ``effort`` and ``prompt_version``.

Exit codes: 0 scored; 2 bad arguments or a malformed file; 3 could not run
(no draw, no labels or no run report; a file error); 4 checks failed (the
alias map is not the replay of the merge log, a label is not for a drawn pair
in its stratum, or, for the scoring set, the latest run used another config
than the draw or a stratum has no labels; the tuning set only warns on these,
since ticket 06 tunes on it).
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

from eval.bootstrap import BOOTSTRAP_RESAMPLES, CONFIDENCE
from graph.ontology import ONTOLOGY_VERSION
from ingest.provenance import git_state
from resolve.label_report import now
from resolve.labeller import (
    LABEL_EFFORT,
    LABEL_MODEL,
    LABEL_NOTE,
    LABEL_PROVIDER,
    LabelPromptError,
    load_label_prompt,
)
from resolve.labels import (
    LabelFileError,
    PairLabel,
    draw_path,
    labels_path,
    read_draw,
    read_json,
    read_labels,
)
from resolve.log import LogError, read_log, replay
from resolve.paths import (
    ALIAS_MAP_NAME,
    MERGE_LOG_NAME,
    OUTPUT_DIR,
    latest_report,
    next_numbered,
    read_alias_map,
)
from resolve.report import write_report
from resolve.sample import SETS, Draw
from resolve.scoring import Decisions, Figure, PairView, ScoreReport, score

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_CHECK_FAILED = 4
_SAME = "same"


class _Stop(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m resolve.score",
                                     description="Score the alias map against labelled pairs.")
    parser.add_argument("--set", dest="set_name", choices=SETS, default="scoring")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc


def main(argv: Sequence[str] | None = None, *, out_dir: Path = OUTPUT_DIR) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    try:
        args = _parse_args(argv)
        return _run(args.set_name, out_dir)
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


@dataclass(frozen=True)
class _Inputs:
    drawn: Draw
    labels: tuple[PairLabel, ...]
    aliases: Mapping[str, str]
    run_report: Mapping[str, object]
    files: Mapping[str, Mapping[str, str | None]]  # name to file name and SHA-256


def _run(set_name: str, out_dir: Path) -> int:
    inputs = _read(set_name, out_dir)
    _check_labels(inputs.drawn, inputs.labels)
    warnings = _check_scorable(inputs)
    adjudication = inputs.run_report.get("adjudication") or {}
    result = score([PairView(lb.key_a, lb.key_b, lb.stratum, lb.same) for lb in inputs.labels],
                   inputs.aliases, inputs.drawn.populations,
                   decisions=_decisions(adjudication))
    if result.unrepresented and set_name == "scoring":
        raise _Stop(EXIT_CHECK_FAILED, "no labels in the stratum "
                    f"{', '.join(s.value for s in result.unrepresented)}; recall would leave "
                    "out the matches the rules missed there")
    warnings += [f"stratum {s.value} has no labels" for s in result.unrepresented]
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    record = {"provenance": {**_provenance(set_name, inputs, adjudication),
                             "warnings": warnings},
              **_body(result, inputs.drawn, inputs.labels)}
    try:
        path = next_numbered(out_dir, f"{set_name}-score")
        write_report(path, record)
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot write the score report: {exc}") from exc
    print(_summary(result, path))
    return EXIT_OK


def _read(set_name: str, out_dir: Path) -> _Inputs:
    draw_file, labels_file = draw_path(out_dir, set_name), labels_path(out_dir, set_name)
    for needed, what in ((draw_file, "draw"), (labels_file, "labels")):
        if not needed.exists():
            raise _Stop(EXIT_RUN_ERROR, f"no {set_name} {what}; run python -m resolve.label "
                                        f"--set {set_name} first")
    try:
        report_path = latest_report(out_dir)
        if report_path is None:
            raise _Stop(EXIT_RUN_ERROR, "no run report; run python -m resolve.run first")
        drawn, labels = read_draw(draw_file), read_labels(labels_file)
        aliases = read_alias_map(out_dir / ALIAS_MAP_NAME)
        replayed = replay(read_log(out_dir / MERGE_LOG_NAME))
        run_report = read_json(report_path)
        files = {name: {"file": p.name, "sha256": _file_hash(p)} for name, p in (
            ("draw", draw_file), ("labels", labels_file),
            ("alias_map", out_dir / ALIAS_MAP_NAME), ("run_report", report_path))}
    except (LabelFileError, LogError, ValueError) as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the inputs: {exc}") from exc
    if aliases != replayed:
        raise _Stop(EXIT_CHECK_FAILED, f"{ALIAS_MAP_NAME} is not the replay of "
                                       f"{MERGE_LOG_NAME}; one was edited by hand")
    return _Inputs(drawn, labels, aliases, run_report, files)


def _check_scorable(inputs: _Inputs) -> list[str]:
    """Stops a scoring set whose strata came from another config than the scored run."""
    run_hash = (inputs.run_report.get("provenance") or {}).get("config_hash")
    if run_hash == inputs.drawn.config_hash:
        return []
    problem = (f"the run report's config {str(run_hash)[:12]} is not the draw's "
               f"{inputs.drawn.config_hash[:12]}, so the strata do not match the scored rules")
    if inputs.drawn.set_name == "scoring":
        raise _Stop(EXIT_CHECK_FAILED, problem)
    return [problem]


def _check_labels(drawn: Draw, labels: Sequence[PairLabel]) -> None:
    strata = {p.keys: p.stratum for p in drawn.pairs}
    wrong = [lb for lb in labels if strata.get(lb.keys) is not lb.stratum
             or lb.set_name != drawn.set_name]
    if wrong:
        raise _Stop(EXIT_CHECK_FAILED, f"{len(wrong)} labels are not for a drawn pair in their "
                                       f"stratum, such as {wrong[0].key_a} / {wrong[0].key_b}")


def _decisions(adjudication: Mapping[str, object]) -> Decisions:
    rows = adjudication.get("decisions") or []
    try:
        return {(r["key_a"], r["key_b"]): None if r["answer"] is None else r["answer"] == _SAME
                for r in rows}
    except (KeyError, TypeError) as exc:
        raise _Stop(EXIT_USAGE, f"the run report's adjudication decisions are malformed: "
                                f"{exc}") from exc


def _file_hash(path: Path) -> str | None:
    """The file's SHA-256; None for a file that may be absent (an empty alias map)."""
    return sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _label_prompt() -> dict[str, object]:
    try:
        prompt = load_label_prompt()
    except LabelPromptError as exc:
        raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
    return {"version": prompt.prompt_version, "sha256": prompt.sha256,
            "file_sha256": prompt.file_sha256}


def _provenance(set_name: str, inputs: _Inputs,
                adjudication: Mapping[str, object]) -> dict[str, object]:
    run = inputs.run_report.get("provenance") or {}
    return {
        "note": LABEL_NOTE, "set": set_name, "commit": git_state(),
        "created_at": now(),
        "labeller": {"provider": LABEL_PROVIDER, "model": LABEL_MODEL, "effort": LABEL_EFFORT,
                     "prompt": _label_prompt(),
                     "prompt_versions_in_labels": sorted({lb.prompt_version
                                                          for lb in inputs.labels})},
        "adjudicator": {k: adjudication.get(k) for k in ("model", "effort", "prompt_version")},
        "config_hash": {"draw": inputs.drawn.config_hash, "run": run.get("config_hash")},
        "ontology_version": ONTOLOGY_VERSION,
        "files": dict(inputs.files),
        "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "confidence": CONFIDENCE,
                      "within_strata": True},
    }


def _figure(figure: Figure) -> dict[str, object]:
    return {"value": figure.value, "interval": figure.interval}


def _body(result: ScoreReport, drawn: Draw, labels: Sequence[PairLabel]) -> dict[str, object]:
    return {
        "precision": _figure(result.precision), "recall": _figure(result.recall),
        "rules_share_of_true_matches": _figure(result.rules_share),
        "adjudicator": {"judged": result.adjudicator.judged,
                        "correct": result.adjudicator.correct,
                        "unusable": result.adjudicator.unusable,
                        "accuracy": _figure(result.adjudicator.accuracy)},
        "strata": [{**asdict(s), "stratum": s.stratum.value} for s in result.strata],
        "unrepresented_strata": [s.value for s in result.unrepresented],
        "counts": {"drawn": len(drawn.pairs), "labelled": len(labels),
                   "unlabelled": len(drawn.pairs) - len(labels),
                   "quotes_unverified": sum(not lb.quotes_verified for lb in labels)},
        "seed": result.seed,
    }


def _show(name: str, figure: Figure) -> str:
    if figure.value is None:
        return f"{name} undefined"
    low, high = (figure.interval or {}).get("low"), (figure.interval or {}).get("high")
    return f"{name} {figure.value:.3f} [{low:.3f}, {high:.3f}]"


def _summary(result: ScoreReport, path: Path) -> str:
    shown = ", ".join(_show(n, f) for n, f in (
        ("precision", result.precision), ("recall", result.recall),
        ("rules share", result.rules_share), ("adjudicator", result.adjudicator.accuracy)))
    return f"{shown} ({result.note}); wrote {path.name}"


if __name__ == "__main__":
    sys.exit(main())
