"""Reading the committed run files and reports behind ``benchmarks/results.md``.

Every file read is recorded as an ``InputFile`` (repo-relative path, SHA-256 and
the commit the file names), so the table's header can list them.  Nothing here
touches the network or a database.

An arm's run files for one split are an ``ArmRuns``.  ``source(class)`` is the
file a class's row reads: a full run holding the class, else a retrieval-only
run holding it, else None.  Two files of one mode holding the same class are
refused, since the table would have to pick one silently.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from eval.results_inputs import RunNames
from ingest.provenance import REPO_ROOT

RUNS_DIR = "benchmarks/runs"
FULL, RETRIEVAL_ONLY = "full", "retrieval_only"


class ResultsInputError(ValueError):
    """An input file is missing, unreadable or inconsistent with the others."""


@dataclass(frozen=True)
class InputFile:
    path: str  # repo-relative
    sha256: str
    commit: str | None  # the commit the file records, when it records one


def read_json(path: str, root: Path = REPO_ROOT) -> tuple[object, InputFile]:
    """A committed JSON file's content and its ``InputFile``."""
    raw = _read(path, root)
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ResultsInputError(f"{path} is not JSON: {exc}") from exc
    return document, InputFile(path, _sha(raw), _commit(document))


def read_jsonl(path: str, root: Path = REPO_ROOT) -> tuple[list[dict], InputFile]:
    """A committed JSON Lines file's rows and its ``InputFile``; the commit is the last
    row's that records one."""
    raw = _read(path, root)
    try:
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    except ValueError as exc:
        raise ResultsInputError(f"{path} is not JSON Lines: {exc}") from exc
    commits = [c for c in (_commit(r) for r in rows) if c]
    return rows, InputFile(path, _sha(raw), commits[-1] if commits else None)


def _read(path: str, root: Path) -> bytes:
    try:
        return (root / path).read_bytes()
    except OSError as exc:
        raise ResultsInputError(f"cannot read {path}: {exc}") from exc


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _commit(document: object) -> str | None:
    if not isinstance(document, Mapping):
        return None
    for holder in (document, document.get("header"), document.get("provenance")):
        if isinstance(holder, Mapping) and isinstance(holder.get("commit"), str):
            return holder["commit"]
    return None


@dataclass(frozen=True)
class RunFile:
    """One results file written by ``eval.run``."""

    input: InputFile
    document: Mapping[str, object]

    @property
    def header(self) -> Mapping[str, object]:
        return self.document["header"]

    @property
    def mode(self) -> str:
        return str(self.header.get("mode", FULL))

    @property
    def arm(self) -> str:
        return str(self.header["arm"])

    def cell(self, class_: str) -> Mapping[str, object] | None:
        """The class's cell in the agent-drafted set, or None when the run lacks it."""
        for arms in self.document["cells"].values():
            found = arms.get(self.arm, {}).get(class_)
            if found is not None:
                return found
        return None

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple(c for arms in self.document["cells"].values()
                     for c in arms.get(self.arm, {}))

    def questions(self) -> list[Mapping[str, object]]:
        return list(self.document["questions"])


@dataclass(frozen=True)
class ArmRuns:
    arm: str
    files: tuple[RunFile, ...]

    def source(self, class_: str) -> RunFile | None:
        """The file a class's row reads: a full run holding it, else a retrieval-only one."""
        for mode in (FULL, RETRIEVAL_ONLY):
            holding = [f for f in self.files if f.mode == mode and class_ in f.classes]
            if len(holding) > 1:
                names = ", ".join(f.input.path for f in holding)
                raise ResultsInputError(f"{self.arm} {class_}: more than one {mode} run ({names})")
            if holding:
                return holding[0]
        return None

    def full_files(self) -> tuple[RunFile, ...]:
        return tuple(f for f in self.files if f.mode == FULL)


Split = Mapping[str, ArmRuns]  # arm -> its runs, in the manifest's arm order


def load_run(name: str, root: Path = REPO_ROOT) -> RunFile:
    document, info = read_json(f"{RUNS_DIR}/{name}", root)
    if not isinstance(document, Mapping) or not isinstance(document.get("header"), Mapping):
        raise ResultsInputError(f"{name} is not a results file")
    return RunFile(info, document)


def load_split(names: RunNames, root: Path = REPO_ROOT) -> dict[str, ArmRuns]:
    """Every arm's runs; a file whose header names another arm is refused."""
    split = {}
    for arm, files in names.items():
        runs = tuple(load_run(n, root) for n in files)
        wrong = [r.input.path for r in runs if r.arm != arm]
        if wrong:
            raise ResultsInputError(f"{', '.join(wrong)} is not a {arm} run")
        split[arm] = ArmRuns(arm, runs)
    return split


def split_inputs(split: Split) -> list[InputFile]:
    return [f.input for runs in split.values() for f in runs.files]
