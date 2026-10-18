"""The router's versioned classifier prompt (Step 13).

Each version is one file, ``prompts/route/{version}.md``, with a ``# System``
part and a ``# User`` template holding ``$question``.  A published file is
frozen: its SHA-256 is pinned below and loading refuses a file that no longer
matches, so an edit has to become a new version.  ``sha256`` hashes the system
prompt, the user template and the output schema, and the run header records
``route/{version}@{sha256[:8]}``.  The few-shot examples are invented, never
eval questions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType

from ingest.provenance import REPO_ROOT

KIND = "route"
ROUTE_PROMPTS_DIR = REPO_ROOT / "prompts" / KIND
PUBLISHED: Mapping[str, str] = MappingProxyType({
    "v1": "cbc04679a6a61c90ca51fcf700acd2c9aa02f28fea0520a046a195c8d3a085a7",
    # v2 (2026-10-18): the dev sweep put all 10 global questions under multi_hop, so the
    # global and multi_hop descriptions now separate a theme from particular facts.
    "v2": "c7860cc94f40b76ecaa6581263cda8ea7c00c8a7d7ac76cfb274ece3500c2780",
})
PREDICTED_CLASSES = ("lookup", "local", "multi_hop", "global", "decline")
_PARTS = re.compile(r"\A# System\n(?P<system>.*?)\n# User\n(?P<user>.*)\Z", re.DOTALL)
_HASH_CHARS = 8
_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reason", "predicted_class"],
    "properties": {
        "reason": {"type": "string", "description": "One or two sentences: why this class."},
        "predicted_class": {"type": "string", "enum": list(PREDICTED_CLASSES)},
    },
}


class RoutePromptError(RuntimeError):
    """A route prompt version is unknown, changed after publication, or malformed."""


@dataclass(frozen=True, slots=True)
class RoutePrompt:
    version: str
    file_sha256: str
    sha256: str  # of the system prompt, the user template and the output schema
    path: str  # relative to the repo, for the results header
    system: str
    user_template: str

    @property
    def prompt_version(self) -> str:
        """The version the run header records, such as ``route/v1@0123abcd``."""
        return f"{KIND}/{self.version}@{self.sha256[:_HASH_CHARS]}"


def route_output_schema() -> dict[str, object]:
    """The classifier's reply schema, reason first; a new dict on every call."""
    return copy.deepcopy(_SCHEMA)


def load_route_prompt(version: str, prompts_dir: Path = ROUTE_PROMPTS_DIR) -> RoutePrompt:
    """The published route prompt *version*; raises ``RoutePromptError`` if its file changed."""
    if version not in PUBLISHED:
        raise RoutePromptError(f"route prompt {version} is not published; "
                               f"known: {sorted(PUBLISHED)}")
    path = prompts_dir / f"{version}.md"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RoutePromptError(f"cannot read route prompt {version}: "
                               f"{type(exc).__name__}") from exc
    file_sha = hashlib.sha256(raw).hexdigest()
    if file_sha != PUBLISHED[version]:
        raise RoutePromptError(f"route {version} has changed since it was published; "
                               "write the change as a new version instead")
    parts = _PARTS.match(raw.decode("utf-8"))
    if parts is None:
        raise RoutePromptError(f"route prompt {version} lacks a '# System' and a '# User' part")
    system, user = parts["system"].strip(), parts["user"].strip()
    try:
        Template(user).substitute(question="")
    except (KeyError, ValueError) as exc:
        raise RoutePromptError(f"route prompt {version}: bad placeholder in the user part") \
            from exc
    sent = json.dumps({"system": system, "user": user, "schema": _SCHEMA}, sort_keys=True,
                      ensure_ascii=False)
    return RoutePrompt(version=version, file_sha256=file_sha,
                       sha256=hashlib.sha256(sent.encode("utf-8")).hexdigest(),
                       path=_display_path(path), system=system, user_template=user)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name
