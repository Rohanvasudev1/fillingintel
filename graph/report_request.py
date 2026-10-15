"""One community report call, as a `ModelRequest` (Step 12).

The config pins live here, as in persist.request: claude-haiku-5-5 at effort
`high`, structured output, no sampling settings, no tools, and the system
prompt as one block with the only cache breakpoint. A lower community's user
message lists its members, member edges and passages (graph.report_input); a
top community's lists its lower reports' kept text.
"""
from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from string import Template

from graph.community import LOWER, TOP, Level
from graph.report_input import (
    ChildReport,
    LowerInput,
    TopInput,
    edge_line,
    member_line,
    passage_block,
)
from graph.report_prompt import ReportPrompt, output_schema
from retrieve.answer_model import Effort
from retrieve.pricing import TABLE_2026_10_10

REPORT_PROVIDER = "anthropic"
REPORT_MODEL = "claude-haiku-5-5"
REPORT_EFFORT: Effort = "high"
REPORT_MAX_TOKENS = 16_000  # thinking plus the reply, as in persist.request
REPORT_PRICES = TABLE_2026_10_10  # Haiku 5.5 at its up-to-100K-token prices
NONE = "(none)"


@dataclass(frozen=True)
class ReportRequest:
    """Everything sent for one community; the ID and level are kept for the caller only."""

    community_id: str
    level: Level
    system: str
    user: str
    model: str = REPORT_MODEL
    effort: Effort = REPORT_EFFORT
    max_tokens: int = REPORT_MAX_TOKENS

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


def build_lower_request(prompt: ReportPrompt, built: LowerInput) -> ReportRequest:
    by_key = {m.key: m for m in built.members}
    user = Template(prompt.lower_template).substitute(
        members="\n".join(member_line(m) for m in built.members),
        edges="\n".join(edge_line(e, by_key) for e in built.edges) or NONE,
        passages="\n\n".join(passage_block(p) for p in built.passages),
    )
    return ReportRequest(built.community_id, LOWER, prompt.system, user)


def report_block(child: ChildReport) -> str:
    title = html.escape(child.title, quote=True)
    return f'<report id="{child.community_id}" title="{title}">\n{child.text}\n</report>'


def build_top_request(prompt: ReportPrompt, built: TopInput) -> ReportRequest:
    """ValueError for a top community with no lower report to read."""
    if not built.children:
        raise ValueError(f"{built.community_id} has no lower report with kept text")
    user = Template(prompt.top_template).substitute(
        reports="\n\n".join(report_block(c) for c in built.children))
    return ReportRequest(built.community_id, TOP, prompt.system, user)
