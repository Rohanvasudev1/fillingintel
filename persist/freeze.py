"""Freeze the persistence settings before the scoring draw (Step 10, ticket 06).

Usage (offline)::

    uv run python -m persist.freeze

Writes ``benchmarks/persistence/frozen.json`` once: the persistence config
hash, the adjudicator prompt (``persist/v1``), the label prompt
(``persist_label/v1``), both models with effort, the draw's seed, near factor
and sizes, and the run report and link file hash being scored. It refuses a
dirty tree (so the commit names the code that drew and scored), a run report
that is incomplete or ran under other settings, a link file the run did not
write, an existing freeze file and an existing draw (the freeze comes first).

Exit codes: 0 frozen; 2 a malformed file; 3 could not run (no run report, a
file error); 4 refused (dirty tree, settings or links differ, already frozen,
already drawn).
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ingest.provenance import UNKNOWN_COMMIT, git_state
from persist.cli import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE
from persist.cli import Stop as _Stop
from persist.config import PersistConfig
from persist.paths import LINKS_NAME, OUTPUT_DIR
from persist.prompt import PromptError, load_prompt
from persist.score_files import DRAW_NAME, FROZEN_NAME, current_pins, file_sha256
from resolve.labeller import LabelPromptError
from resolve.labels import Frozen, LabelFileError, read_json, write_frozen
from resolve.paths import latest_report

DIRTY = "+dirty"


def main(argv: Sequence[str] | None = None, *, out_dir: Path = OUTPUT_DIR,
         commit: Callable[[], str] = git_state, config: PersistConfig | None = None) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    if argv:
        print("python -m persist.freeze takes no arguments", file=sys.stderr)
        return EXIT_USAGE
    try:
        path = _freeze(out_dir, commit(), config or PersistConfig())
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code
    print(f"froze the persistence settings in {path.name}")
    return EXIT_OK


def _freeze(out_dir: Path, commit: str, config: PersistConfig) -> Path:
    if commit.endswith(DIRTY) or commit == UNKNOWN_COMMIT:
        raise _Stop(EXIT_CHECK_FAILED, f"the tree is {commit}; commit first, so the freeze "
                                       "names the code that draws and scores")
    if (out_dir / FROZEN_NAME).exists():
        raise _Stop(EXIT_CHECK_FAILED, f"{FROZEN_NAME} exists; settings are frozen once")
    if (out_dir / DRAW_NAME).exists():
        raise _Stop(EXIT_CHECK_FAILED, f"{DRAW_NAME} exists; the freeze must come before "
                                       "the draw")
    try:
        prompt_version = load_prompt().prompt_version
        pins = current_pins(prompt_version)
        report_path = latest_report(out_dir)
        if report_path is None:
            raise _Stop(EXIT_RUN_ERROR, "no run report; run python -m persist.run first")
        provenance = read_json(report_path).get("provenance")
        if not isinstance(provenance, dict):
            raise LabelFileError(f"{report_path.name} has no provenance object")
        _check_run(report_path.name, provenance, config, prompt_version,
                   file_sha256(out_dir / LINKS_NAME))
        path = out_dir / FROZEN_NAME
        write_frozen(path, Frozen(config.config_hash, pins), {
            "commit": commit, "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_report": report_path.name, "links_sha256": provenance["links_sha256"],
            "config": config.to_dict()})
    except (PromptError, LabelPromptError) as exc:
        raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
    except LabelFileError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read or write the files: {exc}") from exc
    return path


def _check_run(name: str, provenance: dict, config: PersistConfig, prompt_version: str,
               links_sha: str) -> None:
    problems = []
    if provenance.get("status") != "complete":
        problems.append(f"{name} is not a complete run")
    if provenance.get("config_hash") != config.config_hash:
        problems.append(f"{name} ran under config {str(provenance.get('config_hash'))[:12]}, "
                        f"not {config.config_hash[:12]}")
    if provenance.get("prompt_version") != prompt_version:
        problems.append(f"{name} ran with {provenance.get('prompt_version')}, not "
                        f"{prompt_version}")
    if provenance.get("links_sha256") != links_sha:
        problems.append(f"{LINKS_NAME} is not the one {name} wrote")
    if problems:
        raise _Stop(EXIT_CHECK_FAILED, "cannot freeze: " + "; ".join(problems))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
