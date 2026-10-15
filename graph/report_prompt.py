"""The versioned community report prompt (Step 12), shaped like the persist prompt.

Each version is one file, `prompts/report/{version}.md`, with a `# System`
part, a `# Lower` template for a lower community and a `# Top` template for
a top one. A published file is frozen: its SHA-256 is pinned below and
loading refuses a file that no longer matches, so an edit has to become a new
version.

`sha256` hashes everything sent besides the community (the system prompt, both
templates and the output schema). Every report records `prompt_version`,
`report/{version}@{sha256[:8]}`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType

from ingest.provenance import REPO_ROOT

PROMPTS_DIR = REPO_ROOT / "prompts" / "report"
CURRENT_VERSION = "v1"
PUBLISHED = MappingProxyType({
    "v1": "3782ae8a738b4dd14286defd83fb8b378085cf0e9d5533a938374dc3464b4a75",
})
_PARTS = re.compile(r"\A# System\n(?P<system>.*?)\n# Lower\n(?P<lower>.*?)\n# Top\n(?P<top>.*)\Z",
                    re.DOTALL)
_HASH_CHARS = 8
# The placeholders each template may use; graph.report_request fills each one.
LOWER_FIELDS = ("members", "edges", "passages")
TOP_FIELDS = ("reports",)

_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "findings"],
    "properties": {
        "title": {"type": "string", "description": "A short name for the community."},
        "summary": {"type": "string", "description": "Two to four sentences, each ending "
                                                     "with [chunk_id] citations."},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {"text": {"type": "string", "description": "One or two "
                                        "sentences, each ending with [chunk_id] citations."}},
            },
        },
    },
}


def output_schema() -> dict[str, object]:
    """The reply schema; a new dict on every call."""
    return copy.deepcopy(_SCHEMA)


class PromptError(RuntimeError):
    """A prompt version is unknown, changed after publication, or malformed."""


@dataclass(frozen=True, slots=True)
class ReportPrompt:
    version: str
    file_sha256: str
    sha256: str  # of the system prompt, both templates and the output schema
    path: str  # relative to the repo, for run reports
    system: str
    lower_template: str
    top_template: str

    @property
    def prompt_version(self) -> str:
        """The version stored on every report, such as `report/v1@0123abcd`."""
        return f"report/{self.version}@{self.sha256[:_HASH_CHARS]}"


def load_prompt(version: str = CURRENT_VERSION, prompts_dir: Path = PROMPTS_DIR) -> ReportPrompt:
    """The published prompt *version*; raises `PromptError` if its file has changed."""
    if version not in PUBLISHED:
        raise PromptError(f"report prompt {version} is not published; known: {sorted(PUBLISHED)}")
    path = prompts_dir / f"{version}.md"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PromptError(f"cannot read report prompt {version}: {type(exc).__name__}") from exc
    file_sha = hashlib.sha256(raw).hexdigest()
    if file_sha != PUBLISHED[version]:
        raise PromptError(f"report prompt {version} has changed since it was published; "
                          "write the change as a new version instead")
    parts = _PARTS.match(raw.decode("utf-8"))
    if parts is None:
        raise PromptError(f"report prompt {version} lacks a '# System', '# Lower' and '# Top' "
                          "part")
    system, lower, top = (parts[p].strip() for p in ("system", "lower", "top"))
    for template, fields in ((lower, LOWER_FIELDS), (top, TOP_FIELDS)):
        try:
            Template(template).substitute(dict.fromkeys(fields, ""))
        except (KeyError, ValueError) as exc:
            raise PromptError(f"report prompt {version}: bad placeholder in a template") from exc
    return ReportPrompt(version=version, file_sha256=file_sha,
                        sha256=_sent_hash(system, lower, top), path=_display_path(path),
                        system=system, lower_template=lower, top_template=top)


def _sent_hash(system: str, lower: str, top: str) -> str:
    sent = json.dumps({"system": system, "lower": lower, "top": top, "schema": output_schema()},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(sent.encode("utf-8")).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name
