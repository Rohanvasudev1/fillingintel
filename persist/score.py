"""Score the persistence links against the labelled pairs, once (Step 10, ticket 06).

Usage (offline; no API key or database)::

    uv run python -m persist.score

The command checks that the settings and link file still match
``frozen.json``, reads ``scoring-draw.json`` and ``scoring-labels.jsonl``, and
runs persist.scoring: weighted precision and recall of the selected links,
the share of true matches in the candidate stratum and the adjudicator's
accuracy on the labelled pairs it answered, each with a fixed-seed stratified
bootstrap 95% interval, plus raw counts per stratum. The report goes to
``scoring-score1.json``. The links are scored once: a second score is
refused, so a poor score is reported, not tuned. Every figure is
"agent-labelled (gpt-6-luna)".

Exit codes: 0 scored; 2 bad arguments or a malformed file; 3 could not run (no
draw or labels, a file error); 4 checks failed (not frozen, already scored,
the draw is not under the frozen config, a label is not for a drawn pair in
its stratum or under the frozen label prompt, or a stratum has no labels).
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from eval.bootstrap import BOOTSTRAP_RESAMPLES, CONFIDENCE
from graph.ontology import ONTOLOGY_VERSION
from ingest.provenance import git_state
from persist.cli import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE
from persist.cli import Stop as _Stop
from persist.config import PersistConfig
from persist.paths import LINKS_NAME, OUTPUT_DIR
from persist.prompt import PromptError, load_prompt
from persist.sample import SET_NAME, PersistDraw
from persist.score_files import (
    DRAW_NAME,
    FROZEN_NAME,
    LABELS_NAME,
    SCORE_PREFIX,
    FrozenRun,
    NotFrozen,
    file_sha256,
    frozen_run,
    read_draw,
)
from persist.scoring import NAMESPACE, score_links
from resolve.labeller import LABEL_EFFORT, LABEL_MODEL, LABEL_NOTE, LABEL_PROVIDER
from resolve.labels import LabelFileError, PairLabel, read_labels
from resolve.paths import next_numbered
from resolve.report import write_report
from resolve.score import score_body, score_summary
from resolve.scoring import Decisions, PairView

_SAME = "same"


def main(argv: Sequence[str] | None = None, *, out_dir: Path = OUTPUT_DIR,
         commit: Callable[[], str] = git_state, config: PersistConfig | None = None) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    if argv:
        print("python -m persist.score takes no arguments", file=sys.stderr)
        return EXIT_USAGE
    try:
        return _run(out_dir, commit, config or PersistConfig())
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


def _run(out_dir: Path, commit: Callable[[], str], config: PersistConfig) -> int:
    if scored := sorted(out_dir.glob(f"{SCORE_PREFIX}*.json")):
        raise _Stop(EXIT_CHECK_FAILED, f"{scored[0].name} exists; the links are scored once")
    run = _frozen(out_dir, config)
    drawn, labels = _read(out_dir)
    _check(drawn, labels, run)
    result = score_links([PairView(lb.key_a, lb.key_b, lb.stratum, lb.same) for lb in labels],
                         [(lk.earlier_key, lk.later_key) for lk in run.links],
                         drawn.populations, decisions=_decisions(run))
    if result.unrepresented:
        raise _Stop(EXIT_CHECK_FAILED, "no labels in the stratum "
                    f"{', '.join(s.value for s in result.unrepresented)}; recall would leave "
                    "out the links missed there")
    try:
        record = {"provenance": _provenance(out_dir, run, commit()),
                  **score_body(result, len(drawn.pairs), labels)}
        path = next_numbered(out_dir, SCORE_PREFIX)
        write_report(path, record)
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot write the score report: {exc}") from exc
    print(score_summary(result, path))
    return EXIT_OK


def _frozen(out_dir: Path, config: PersistConfig) -> FrozenRun:
    try:
        return frozen_run(out_dir, config.config_hash, load_prompt().prompt_version)
    except NotFrozen as exc:
        raise _Stop(EXIT_CHECK_FAILED, str(exc)) from exc
    except (LabelFileError, ValueError) as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except (PromptError, OSError) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the frozen run: {exc}") from exc


def _read(out_dir: Path) -> tuple[PersistDraw, tuple[PairLabel, ...]]:
    for name in (DRAW_NAME, LABELS_NAME):
        if not (out_dir / name).exists():
            raise _Stop(EXIT_RUN_ERROR, f"no {name}; run python -m persist.label first")
    try:
        return read_draw(out_dir / DRAW_NAME), read_labels(out_dir / LABELS_NAME)
    except LabelFileError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the draw or labels: {exc}") from exc


def _check(drawn: PersistDraw, labels: Sequence[PairLabel], run: FrozenRun) -> None:
    if drawn.config_hash != run.frozen.config_hash:
        raise _Stop(EXIT_CHECK_FAILED, f"the draw's config {drawn.config_hash[:12]} is not the "
                                       f"frozen {run.frozen.config_hash[:12]}")
    strata = {p.keys: p.stratum for p in drawn.pairs}
    label_prompt = run.frozen.prompts.get("label")
    wrong = [lb for lb in labels if strata.get(lb.keys) is not lb.stratum
             or lb.set_name != SET_NAME or lb.prompt_version != label_prompt]
    if wrong:
        raise _Stop(EXIT_CHECK_FAILED, f"{len(wrong)} labels are not for a drawn pair in their "
                                       f"stratum under {label_prompt}, such as "
                                       f"{wrong[0].key_a} / {wrong[0].key_b}")


def _decisions(run: FrozenRun) -> Decisions:
    try:
        return {(d["earlier_key"], d["later_key"]):
                None if d["answer"] is None else d["answer"] == _SAME
                for d in run.report.get("decisions") or []}
    except (KeyError, TypeError) as exc:
        raise _Stop(EXIT_USAGE, f"{run.report_name}'s decisions are malformed: {exc}") from exc


def _provenance(out_dir: Path, run: FrozenRun, commit: str) -> dict[str, object]:
    report = run.report.get("provenance") or {}
    files = {name: {"file": file, "sha256": file_sha256(out_dir / file)} for name, file in (
        ("frozen", FROZEN_NAME), ("draw", DRAW_NAME), ("labels", LABELS_NAME),
        ("links", LINKS_NAME), ("run_report", run.report_name))}
    return {
        "note": LABEL_NOTE, "set": SET_NAME, "commit": commit,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "labeller": {"provider": LABEL_PROVIDER, "model": LABEL_MODEL, "effort": LABEL_EFFORT,
                     "prompt_version": run.frozen.prompts.get("label")},
        "adjudicator": {k: report.get(k) for k in ("model", "effort", "prompt_version")},
        "config_hash": run.frozen.config_hash, "config": report.get("config"),
        "ontology_version": ONTOLOGY_VERSION, "files": files,
        "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "confidence": CONFIDENCE,
                      "within_strata": True, "namespace": NAMESPACE},
    }


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
