"""One adjudicator call per candidate pair, as a `ModelRequest` (Step 9).

The config pins live here: provider, model, effort, output cap and the dated
price table. The request goes through the existing Anthropic client and
response cache with structured output, no sampling settings and no tools. The
system prompt is one block with the only cache breakpoint, as in extraction.

The user message gives each node's name, label and evidence spans, and the
text resolve() returns for its first MAX_CHUNKS_PER_NODE evidence chunks; a
chunk both nodes cite is printed once. Those chunks are the node's own for the
quote check, so a quote can only come from text the model saw.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from string import Template

from extract.spans import unescape
from resolve.nodes import EvidenceRef, ResolveNode
from resolve.output_schema import output_schema
from resolve.prompt import ResolvePrompt
from retrieve.answer_model import Effort
from retrieve.pricing import TABLE_2026_10_09

ADJUDICATOR_PROVIDER = "anthropic"
ADJUDICATOR_MODEL = "claude-sonnet-5-5"
ADJUDICATOR_EFFORT: Effort = "high"
ADJUDICATOR_MAX_TOKENS = 8_000  # thinking plus the reply
ADJUDICATION_PRICES = TABLE_2026_10_09  # Sonnet 5.5 cache reads at $0.10 per MTok
MAX_CHUNKS_PER_NODE = 3


@dataclass(frozen=True)
class AdjudicateRequest:
    """Everything sent for one pair; the keys are kept for the caller and tracing only."""

    key_a: str
    key_b: str
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


def sent_chunks(node: ResolveNode) -> tuple[str, ...]:
    """The chunk IDs whose text is sent for *node*: its first evidence chunks."""
    return node.chunk_ids[:MAX_CHUNKS_PER_NODE]


def build_request(prompt: ResolvePrompt, a: ResolveNode, b: ResolveNode,
                  texts: Mapping[str, str]) -> AdjudicateRequest:
    """The request for the pair (*a*, *b*); ValueError when a chunk's text is missing."""
    chunk_ids = tuple(dict.fromkeys((*sent_chunks(a), *sent_chunks(b))))
    if missing := [c for c in chunk_ids if c not in texts]:
        raise ValueError(f"no text for chunk {missing[0]} ({len(missing)} missing)")
    user = Template(prompt.user_template).substitute(
        label=a.label, node_a=_describe(a), node_b=_describe(b),
        passages="\n\n".join(_passage(c, texts[c]) for c in chunk_ids),
    )
    return AdjudicateRequest(key_a=a.key, key_b=b.key, system=prompt.system, user=user)


def _describe(node: ResolveNode) -> str:
    sent = sent_chunks(node)
    lines = [f"name: {unescape(node.name)}"]
    if node.metric is not None:
        m = node.metric
        lines.append(f"figure: {m.value:g} {m.unit}, period {m.period}, subject {m.subject_key}")
    lines.append(f"passages: {', '.join(sent)}")
    lines.append("evidence:")
    lines += [f'- [{e.chunk_id}] "{e.span}"' for e in _sent_evidence(node.evidence, sent)]
    return "\n".join(lines)


def _sent_evidence(evidence: Sequence[EvidenceRef], sent: Sequence[str]) -> list[EvidenceRef]:
    return [e for e in dict.fromkeys(evidence) if e.chunk_id in sent]


def _passage(chunk_id: str, text: str) -> str:
    return f'<passage id="{chunk_id}">\n{text}\n</passage>'
