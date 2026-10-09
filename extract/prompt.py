"""The versioned extract prompt (Step 8), shaped like the answer prompt.

Each version is one file, `prompts/extract/{version}.md`, with a `# System`
part holding `$ontology` and a `# User` template. A published file is frozen:
its SHA-256 is pinned below and loading refuses a file that no longer matches,
so an edit has to become a new version.

The system prompt is the file with the generated schema text in place of
`$ontology`. `sha256` hashes everything sent besides the chunk (the rendered
system prompt, the user template and the output schema), so an ontology change
gives a new prompt version even when the file is unchanged. Every piece of
evidence records `prompt_version`, `extract/{version}@{sha256[:8]}`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType

from extract.output_schema import output_schema
from graph.schema_text import schema_text
from ingest.provenance import REPO_ROOT

PROMPTS_DIR = REPO_ROOT / "prompts" / "extract"
CURRENT_VERSION = "v1"
PUBLISHED = MappingProxyType({
    "v1": "4b83bf9bbe7767aa81567e8983d298f2948accacca124b493ec3e7eb96ffd49d",
})
_PARTS = re.compile(r"\A# System\n(?P<system>.*?)\n# User\n(?P<user>.*)\Z", re.DOTALL)
_HASH_CHARS = 8
# The placeholders the user template may use; extract.request fills each one.
USER_FIELDS = ("company", "ticker", "form_type", "fiscal_period", "section", "text")


class PromptError(RuntimeError):
    """A prompt version is unknown, changed after publication, or malformed."""


@dataclass(frozen=True, slots=True)
class ExtractPrompt:
    version: str
    file_sha256: str
    sha256: str  # of the rendered system prompt, user template and output schema
    path: str  # relative to the repo, for run reports
    system: str
    user_template: str

    @property
    def prompt_version(self) -> str:
        """The version stored on every piece of evidence, such as `extract/v1@0123abcd`."""
        return f"extract/{self.version}@{self.sha256[:_HASH_CHARS]}"


def load_prompt(version: str = CURRENT_VERSION, prompts_dir: Path = PROMPTS_DIR) -> ExtractPrompt:
    """The published prompt *version*; raises `PromptError` if its file has changed."""
    if version not in PUBLISHED:
        raise PromptError(f"extract prompt {version} is not published; known: {sorted(PUBLISHED)}")
    path = prompts_dir / f"{version}.md"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PromptError(f"cannot read extract prompt {version}: {type(exc).__name__}") from exc
    file_sha = hashlib.sha256(raw).hexdigest()
    if file_sha != PUBLISHED[version]:
        raise PromptError(
            f"extract prompt {version} has changed since it was published; "
            "write the change as a new version instead"
        )
    parts = _PARTS.match(raw.decode("utf-8"))
    if parts is None:
        raise PromptError(f"extract prompt {version} lacks a '# System' and a '# User' part")
    try:
        system = Template(parts["system"].strip()).substitute(ontology=schema_text().strip())
    except (KeyError, ValueError) as exc:
        raise PromptError(f"extract prompt {version}: bad placeholder in the system part") from exc
    user_template = parts["user"].strip()
    try:
        Template(user_template).substitute(dict.fromkeys(USER_FIELDS, ""))
    except (KeyError, ValueError) as exc:
        raise PromptError(f"extract prompt {version}: bad placeholder in the user part") from exc
    return ExtractPrompt(
        version=version,
        file_sha256=file_sha,
        sha256=_sent_hash(system, user_template),
        path=_display_path(path),
        system=system,
        user_template=user_template,
    )


def _sent_hash(system: str, user_template: str) -> str:
    sent = json.dumps(
        {"system": system, "user": user_template, "schema": output_schema()},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(sent.encode("utf-8")).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name
