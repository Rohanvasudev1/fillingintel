"""The versioned answer prompt (Step 5).

Each version is one file, ``prompts/answer/{version}.md``, with a ``# System``
part and a ``# User`` template holding ``$excerpts`` and ``$question``.  A
published version is frozen: its SHA-256 is pinned below and loading refuses a
file that no longer matches, so an edit has to become a new version.  Every
results header records the version and hash.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType

from ingest.provenance import REPO_ROOT

PROMPTS_DIR = REPO_ROOT / "prompts" / "answer"
CURRENT_VERSION = "v1"
PUBLISHED = MappingProxyType({
    "v1": "dd19391c5ce5de6e3c58106f6f9a9ae4c3f2a7164ddf1c4c7b93c31ec1c2da10",
})
_PARTS = re.compile(r"\A# System\n(?P<system>.*?)\n# User\n(?P<user>.*)\Z", re.DOTALL)


class PromptError(RuntimeError):
    """A prompt version is unknown, changed after publication, or malformed."""


@dataclass(frozen=True)
class AnswerPrompt:
    version: str
    sha256: str
    path: str  # relative to the repo, for the results header
    system: str
    user_template: str


@dataclass(frozen=True)
class SourceChunk:
    """One retrieved chunk as the answer model sees it."""

    chunk_id: str
    company: str
    form_type: str
    fiscal_period: str
    section: str
    text: str


def load_prompt(version: str = CURRENT_VERSION, prompts_dir: Path = PROMPTS_DIR) -> AnswerPrompt:
    """The published prompt *version*; raises ``PromptError`` if its file has changed."""
    if version not in PUBLISHED:
        raise PromptError(f"answer prompt {version} is not published; known: {sorted(PUBLISHED)}")
    path = prompts_dir / f"{version}.md"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PromptError(f"cannot read answer prompt {version}: {type(exc).__name__}") from exc
    sha = hashlib.sha256(raw).hexdigest()
    if sha != PUBLISHED[version]:
        raise PromptError(
            f"answer prompt {version} has changed since it was published; "
            "write the change as a new version instead"
        )
    parts = _PARTS.match(raw.decode("utf-8"))
    if parts is None:
        raise PromptError(f"answer prompt {version} lacks a '# System' and a '# User' part")
    return AnswerPrompt(
        version=version,
        sha256=sha,
        path=_display_path(path),
        system=parts["system"].strip(),
        user_template=parts["user"].strip(),
    )


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def _excerpt(chunk: SourceChunk) -> str:
    return (
        f'<excerpt id="{chunk.chunk_id}" company="{chunk.company}" form="{chunk.form_type}" '
        f'period="{chunk.fiscal_period}" section="{chunk.section}">\n{chunk.text}\n</excerpt>'
    )


def render_user(prompt: AnswerPrompt, question: str, chunks: Sequence[SourceChunk]) -> str:
    """The user message: every chunk, in retrieval order, then the question."""
    excerpts = "\n\n".join(_excerpt(c) for c in chunks)
    return Template(prompt.user_template).substitute(excerpts=excerpts, question=question)
