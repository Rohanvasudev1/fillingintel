"""Which code produced an artifact: the git commit, marked dirty when the tree has changes."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_GIT_TIMEOUT_SECONDS = 10
# Untracked files here change what the code does (a new module); elsewhere they
# are scratch output (.coverage, spikes/) and do not make a run "dirty".
_CODE_PATHS = ("ingest", "db", "tests", "scripts", "pyproject.toml", "uv.lock")


def git_state() -> str:
    """``<short commit>``, plus ``+dirty`` for tracked changes or untracked code files."""
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
            timeout=_GIT_TIMEOUT_SECONDS,
        ).stdout.strip()

    try:
        commit = git("rev-parse", "--short", "HEAD")
        changed = git("status", "--porcelain", "--untracked-files=no")
        new_code = git("status", "--porcelain", "--untracked-files=all", "--", *_CODE_PATHS)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{commit}{'+dirty' if changed or new_code else ''}"
