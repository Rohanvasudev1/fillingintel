"""One extraction call per chunk, as a `ModelRequest` (Step 8).

The request goes through the existing Anthropic client and response cache. It
carries `claude-sonnet-5-5` at effort `high`, `max_tokens` 8,000 (thinking plus
the reply), structured output with the schema derived from the ontology, no
sampling settings and no tools. The system prompt is one block with the only
cache breakpoint, since it is identical on every call; the chunk is in the
user message. The cache key hashes the whole request, so a prompt edit misses
the response cache by construction.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from string import Template

from extract.filers import FilingInfo
from extract.output_schema import output_schema
from extract.prompt import ExtractPrompt
from retrieve.answer_model import Effort

EXTRACT_MODEL = "claude-sonnet-5-5"
EXTRACT_EFFORT: Effort = "high"
EXTRACT_MAX_TOKENS = 8_000


@dataclass(frozen=True, slots=True)
class ChunkInput:
    """One chunk to extract: its ID, section and the text resolve() returns."""

    chunk_id: str
    section: str
    text: str


@dataclass(frozen=True)
class ExtractRequest:
    """Everything sent for one chunk; `chunk_id` is not sent, only kept for the caller."""

    chunk_id: str
    system: str
    user: str
    model: str = EXTRACT_MODEL
    effort: Effort = EXTRACT_EFFORT
    max_tokens: int = EXTRACT_MAX_TOKENS

    def params(self) -> dict[str, object]:
        """The keyword arguments for `messages.create`."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [
                {"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{"role": "user", "content": self.user}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": output_schema()},
            },
        }

    def cache_key(self) -> str:
        """SHA-256 of the full request."""
        canonical = json.dumps(self.params(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_request(prompt: ExtractPrompt, filing: FilingInfo, chunk: ChunkInput) -> ExtractRequest:
    """The request for one chunk of *filing*."""
    user = Template(prompt.user_template).substitute(
        company=filing.company_name,
        ticker=filing.ticker,
        form_type=filing.form_type,
        fiscal_period=filing.fiscal_period,
        section=chunk.section,
        text=chunk.text,
    )
    return ExtractRequest(chunk_id=chunk.chunk_id, system=prompt.system, user=user)
