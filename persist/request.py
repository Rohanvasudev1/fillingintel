"""One persistence adjudicator call per candidate pair, as a `ModelRequest` (Step 10).

The config pins live here, as in resolve.request: claude-haiku-5-5 at effort
`high`, structured output, no sampling settings, no tools, and the system
prompt as one block with the only cache breakpoint. The user message gives
each risk factor's title and evidence spans, and the text resolve() returns
for its first MAX_CHUNKS_PER_FACTOR evidence chunks, the earlier factor's
first. Those chunks are the factor's own for the quote check.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from string import Template

from extract.spans import unescape
from persist.output_schema import output_schema
from persist.prompt import PersistPrompt
from resolve.nodes import ResolveNode
from retrieve.answer_model import Effort
from retrieve.pricing import TABLE_2026_10_10

ADJUDICATOR_PROVIDER = "anthropic"
ADJUDICATOR_MODEL = "claude-haiku-5-5"
ADJUDICATOR_EFFORT: Effort = "high"
ADJUDICATOR_MAX_TOKENS = 16_000  # thinking plus the reply, as in resolve.request
ADJUDICATION_PRICES = TABLE_2026_10_10  # Haiku 5.5 at its up-to-100K-token prices
MAX_CHUNKS_PER_FACTOR = 3


@dataclass(frozen=True)
class PersistRequest:
    """Everything sent for one pair; the keys are kept for the caller only."""

    later_key: str
    earlier_key: str
    system: str
    user: str
    model: str = ADJUDICATOR_MODEL
    effort: Effort = ADJUDICATOR_EFFORT
    max_tokens: int = ADJUDICATOR_MAX_TOKENS

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


def sent_chunks(factor: ResolveNode) -> tuple[str, ...]:
    """The chunk IDs whose text is sent for *factor*: its first evidence chunks."""
    return factor.chunk_ids[:MAX_CHUNKS_PER_FACTOR]


def build_request(prompt: PersistPrompt, later: ResolveNode, earlier: ResolveNode,
                  texts: Mapping[str, str]) -> PersistRequest:
    """The request for (*later*, *earlier*); ValueError when a chunk's text is missing."""
    chunk_ids = tuple(dict.fromkeys((*sent_chunks(earlier), *sent_chunks(later))))
    if missing := [c for c in chunk_ids if c not in texts]:
        raise ValueError(f"no text for chunk {missing[0]} ({len(missing)} missing)")
    user = Template(prompt.user_template).substitute(
        earlier=_describe(earlier), later=_describe(later),
        passages="\n\n".join(f'<passage id="{c}">\n{texts[c]}\n</passage>' for c in chunk_ids),
    )
    return PersistRequest(later_key=later.key, earlier_key=earlier.key, system=prompt.system,
                          user=user)


def _describe(factor: ResolveNode) -> str:
    sent = sent_chunks(factor)
    lines = [f"title: {unescape(factor.name)}", f"passages: {', '.join(sent)}", "evidence:"]
    lines += [f'- [{e.chunk_id}] "{e.span}"' for e in dict.fromkeys(factor.evidence)
              if e.chunk_id in sent]
    return "\n".join(lines)
