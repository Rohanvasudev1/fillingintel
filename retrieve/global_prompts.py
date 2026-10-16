"""graph_global's versioned map and reduce prompts (Step 12, ADR-0008).

Each version is one file, ``prompts/global_map/{version}.md`` or
``prompts/global_reduce/{version}.md``, with a ``# System`` part and a
``# User`` template.  A published file is frozen: its SHA-256 is pinned below
and loading refuses a file that no longer matches, so an edit has to become a
new version.

``sha256`` hashes everything sent besides the question's own content (the
system prompt, the user template and, for the map prompt, the output schema),
and the run header records ``global_map/{version}@{sha256[:8]}``.  The reduce
prompt keeps ``answer/v1``'s status line and rules, so the answer filter reads
its reply unchanged.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType

from ingest.provenance import REPO_ROOT

MAP = "global_map"
REDUCE = "global_reduce"
MAP_PROMPTS_DIR = REPO_ROOT / "prompts" / MAP
REDUCE_PROMPTS_DIR = REPO_ROOT / "prompts" / REDUCE
CURRENT_VERSION = "v1"
PUBLISHED: Mapping[str, Mapping[str, str]] = MappingProxyType({
    MAP: MappingProxyType({
        "v1": "6b471a524a5b66bb037a98492631cc45bf92f2378222bf622f7ed38e76341834"}),
    REDUCE: MappingProxyType({
        "v1": "382a4090a6798fc933401c42e5d1d74d9fd21bdc73d311ecc3d0b03b4106606e"}),
})
# The placeholders each user template may use; retrieve.global_map and global_reduce fill them.
MAP_FIELDS = ("title", "report", "excerpts", "question")
REDUCE_FIELDS = ("points", "question")
SCORE_RANGE = (0, 100)  # checked in code: structured outputs take no minimum or maximum
_PARTS = re.compile(r"\A# System\n(?P<system>.*?)\n# User\n(?P<user>.*)\Z", re.DOTALL)
_HASH_CHARS = 8
_MAP_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["points"],
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "score"],
                "properties": {
                    "text": {"type": "string", "description": "One or two sentences, each "
                                                              "ending with [chunk_id] citations."},
                    "score": {"type": "integer", "description": "0 to 100: how much the point "
                                                                "helps answer the question."},
                },
            },
        },
    },
}


class PromptError(RuntimeError):
    """A prompt version is unknown, changed after publication, or malformed."""


@dataclass(frozen=True, slots=True)
class GlobalPrompt:
    kind: str  # MAP or REDUCE
    version: str
    file_sha256: str
    sha256: str  # of the system prompt, the user template and any output schema
    path: str  # relative to the repo, for the results header
    system: str
    user_template: str

    @property
    def prompt_version(self) -> str:
        """The version the run header records, such as ``global_map/v1@0123abcd``."""
        return f"{self.kind}/{self.version}@{self.sha256[:_HASH_CHARS]}"


def map_output_schema() -> dict[str, object]:
    """The map reply schema; a new dict on every call."""
    return copy.deepcopy(_MAP_SCHEMA)


def load_map_prompt(version: str = CURRENT_VERSION,
                    prompts_dir: Path = MAP_PROMPTS_DIR) -> GlobalPrompt:
    """The published map prompt *version*; raises ``PromptError`` if its file has changed."""
    return _load(MAP, version, prompts_dir, MAP_FIELDS, map_output_schema())


def load_reduce_prompt(version: str = CURRENT_VERSION,
                       prompts_dir: Path = REDUCE_PROMPTS_DIR) -> GlobalPrompt:
    """The published reduce prompt *version*; raises ``PromptError`` if its file has changed."""
    return _load(REDUCE, version, prompts_dir, REDUCE_FIELDS, None)


def _load(kind: str, version: str, prompts_dir: Path, fields: Sequence[str],
          schema: dict[str, object] | None) -> GlobalPrompt:
    published = PUBLISHED[kind]
    if version not in published:
        raise PromptError(f"{kind} prompt {version} is not published; known: {sorted(published)}")
    path = prompts_dir / f"{version}.md"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PromptError(f"cannot read {kind} prompt {version}: {type(exc).__name__}") from exc
    file_sha = hashlib.sha256(raw).hexdigest()
    if file_sha != published[version]:
        raise PromptError(f"{kind} {version} has changed since it was published; "
                          "write the change as a new version instead")
    parts = _PARTS.match(raw.decode("utf-8"))
    if parts is None:
        raise PromptError(f"{kind} prompt {version} lacks a '# System' and a '# User' part")
    system, user = parts["system"].strip(), parts["user"].strip()
    try:
        Template(user).substitute(dict.fromkeys(fields, ""))
    except (KeyError, ValueError) as exc:
        raise PromptError(f"{kind} prompt {version}: bad placeholder in the user part") from exc
    sent = json.dumps({"system": system, "user": user, "schema": schema}, sort_keys=True,
                      ensure_ascii=False)
    return GlobalPrompt(kind=kind, version=version, file_sha256=file_sha,
                        sha256=hashlib.sha256(sent.encode("utf-8")).hexdigest(),
                        path=_display_path(path), system=system, user_template=user)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name
