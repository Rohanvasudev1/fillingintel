"""The versioned Cypher prompt for graph_traversal (Step 11), shaped like the extract prompt.

Each version is one file, ``prompts/cypher/{version}.md``, with a ``# System``
part holding ``$schema`` and ``$filings``, a ``# User`` template and a
``# Repair`` template.  A published file is frozen: its SHA-256 is pinned below
and loading refuses a file that no longer matches, so an edit has to become a
new version.

The system prompt is the file with the generated schema text and the corpus's
filing table filled in.  ``sha256`` hashes everything sent besides the question
(the rendered system prompt, both templates and the output schema), so an
ontology change or another filing list gives a new prompt version even when the
file is unchanged.  The run header records ``cypher/{version}@{sha256[:8]}``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType

from graph.schema_text import schema_text
from ingest.provenance import REPO_ROOT
from retrieve.question_filter import TICKERS, CorpusFiling

PROMPTS_DIR = REPO_ROOT / "prompts" / "cypher"
CURRENT_VERSION = "v1"
PUBLISHED = MappingProxyType({
    "v1": "26c2658f2d1b89f63a931e246c26a98f30c413ee2c2775dd32c4272d1fabf9fb",
})
_PARTS = re.compile(
    r"\A# System\n(?P<system>.*?)\n# User\n(?P<user>.*?)\n# Repair\n(?P<repair>.*)\Z", re.DOTALL)
_HASH_CHARS = 8
# The placeholders each template may use; retrieve.cypher_request fills them.
USER_FIELDS = ("question", "filter")
REPAIR_FIELDS = ("query", "message")
_TABLE_HEADER = ("| ticker | form | fiscal_period | accession_no | period end |\n"
                 "|---|---|---|---|---|")
_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reasoning", "cypher"],
    "properties": {
        "reasoning": {"type": "string", "description": "Which nodes and edges hold the evidence "
                                                       "and how the query reaches them."},
        "cypher": {"type": "string", "description": "One read-only Cypher query returning a "
                                                    "chunk_ids column."},
    },
}


class PromptError(RuntimeError):
    """A prompt version is unknown, changed after publication, or malformed."""


@dataclass(frozen=True, slots=True)
class CypherPrompt:
    version: str
    file_sha256: str
    sha256: str  # of the rendered system prompt, both templates and the output schema
    path: str  # relative to the repo, for run reports
    system: str
    user_template: str
    repair_template: str

    @property
    def prompt_version(self) -> str:
        """The version the run header records, such as ``cypher/v1@0123abcd``."""
        return f"cypher/{self.version}@{self.sha256[:_HASH_CHARS]}"


def output_schema() -> dict[str, object]:
    """The reply schema, reasoning before the query; a new dict on every call."""
    return copy.deepcopy(_SCHEMA)


def filing_table(filings: Sequence[CorpusFiling]) -> str:
    """The corpus's filings as a Markdown table, by company, then period end."""
    rows = sorted(filings, key=lambda f: (TICKERS.index(f.ticker), f.report_date, f.form_type))
    lines = [f"| {f.ticker} | {f.form_type} | {f.fiscal_period} | {f.accession_no} | "
             f"{f.report_date.isoformat()} |" for f in rows]
    return "\n".join((_TABLE_HEADER, *lines))


def load_prompt(version: str = CURRENT_VERSION, *, filings: Sequence[CorpusFiling],
                prompts_dir: Path = PROMPTS_DIR) -> CypherPrompt:
    """The published prompt *version* for *filings*; raises ``PromptError`` if its file
    has changed."""
    if version not in PUBLISHED:
        raise PromptError(f"cypher prompt {version} is not published; known: {sorted(PUBLISHED)}")
    path = prompts_dir / f"{version}.md"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PromptError(f"cannot read cypher prompt {version}: {type(exc).__name__}") from exc
    file_sha = hashlib.sha256(raw).hexdigest()
    if file_sha != PUBLISHED[version]:
        raise PromptError(f"cypher prompt {version} has changed since it was published; "
                          "write the change as a new version instead")
    parts = _PARTS.match(raw.decode("utf-8"))
    if parts is None:
        raise PromptError(f"cypher prompt {version} lacks '# System', '# User' and "
                          "'# Repair' parts")
    try:
        system = Template(parts["system"].strip()).substitute(
            schema=schema_text().strip(), filings=filing_table(filings))
    except (KeyError, ValueError) as exc:
        raise PromptError(f"cypher prompt {version}: bad placeholder in the system part") from exc
    user, repair = parts["user"].strip(), parts["repair"].strip()
    for name, template, names in (("user", user, USER_FIELDS), ("repair", repair, REPAIR_FIELDS)):
        try:
            Template(template).substitute(dict.fromkeys(names, ""))
        except (KeyError, ValueError) as exc:
            raise PromptError(f"cypher prompt {version}: bad placeholder in the {name} "
                              "part") from exc
    return CypherPrompt(version=version, file_sha256=file_sha,
                        sha256=_sent_hash(system, user, repair), path=_display_path(path),
                        system=system, user_template=user, repair_template=repair)


def _sent_hash(system: str, user: str, repair: str) -> str:
    sent = json.dumps({"system": system, "user": user, "repair": repair,
                       "schema": output_schema()}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(sent.encode("utf-8")).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name
