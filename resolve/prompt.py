"""The versioned adjudicator prompt (Step 9), shaped like the extract prompt.

Each version is one file, `prompts/resolve/{version}.md`, with a `# System`
part and a `# User` template. A published file is frozen: its SHA-256 is pinned
below and loading refuses a file that no longer matches, so an edit has to
become a new version.

`sha256` hashes everything sent besides the pair (the system prompt, the user
template and the output schema). Every decision records `prompt_version`,
`resolve/{version}@{sha256[:8]}`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType

from ingest.provenance import REPO_ROOT
from resolve.output_schema import output_schema

PROMPTS_DIR = REPO_ROOT / "prompts" / "resolve"
CURRENT_VERSION = "v1"
PUBLISHED = MappingProxyType({
    "v1": "403e2d11364e0bef8476fe40be4be3e31cb7c747fbd29dc23ad8b50373a22f8e",
})
_PARTS = re.compile(r"\A# System\n(?P<system>.*?)\n# User\n(?P<user>.*)\Z", re.DOTALL)
_HASH_CHARS = 8
# The placeholders the user template may use; resolve.request fills each one.
USER_FIELDS = ("label", "node_a", "node_b", "passages")


class PromptError(RuntimeError):
    """A prompt version is unknown, changed after publication, or malformed."""


@dataclass(frozen=True, slots=True)
class ResolvePrompt:
    version: str
    file_sha256: str
    sha256: str  # of the system prompt, user template and output schema
    path: str  # relative to the repo, for run reports
    system: str
    user_template: str

    @property
    def prompt_version(self) -> str:
        """The version stored on every decision, such as `resolve/v1@0123abcd`."""
        return f"resolve/{self.version}@{self.sha256[:_HASH_CHARS]}"


def load_prompt(version: str = CURRENT_VERSION, prompts_dir: Path = PROMPTS_DIR) -> ResolvePrompt:
    """The published prompt *version*; raises `PromptError` if its file has changed."""
    if version not in PUBLISHED:
        raise PromptError(f"resolve prompt {version} is not published; known: {sorted(PUBLISHED)}")
    path = prompts_dir / f"{version}.md"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PromptError(f"cannot read resolve prompt {version}: {type(exc).__name__}") from exc
    file_sha = hashlib.sha256(raw).hexdigest()
    if file_sha != PUBLISHED[version]:
        raise PromptError(f"resolve prompt {version} has changed since it was published; "
                          "write the change as a new version instead")
    parts = _PARTS.match(raw.decode("utf-8"))
    if parts is None:
        raise PromptError(f"resolve prompt {version} lacks a '# System' and a '# User' part")
    system, user_template = parts["system"].strip(), parts["user"].strip()
    try:
        Template(user_template).substitute(dict.fromkeys(USER_FIELDS, ""))
    except (KeyError, ValueError) as exc:
        raise PromptError(f"resolve prompt {version}: bad placeholder in the user part") from exc
    return ResolvePrompt(version=version, file_sha256=file_sha,
                         sha256=_sent_hash(system, user_template), path=_display_path(path),
                         system=system, user_template=user_template)


def _sent_hash(system: str, user_template: str) -> str:
    sent = json.dumps({"system": system, "user": user_template, "schema": output_schema()},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(sent.encode("utf-8")).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name
