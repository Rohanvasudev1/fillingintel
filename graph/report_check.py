"""The citation check on a community report (Step 12, invariant 3).

A report's summary and findings are split into sentences by the answer
filter's rules (retrieve.citations). A sentence is kept only when it cites at
least one chunk and every chunk it cites is in the community's evidence: for
a lower community, its members' evidence chunks; for a top one, the chunks its
lower reports kept. Dropped sentences are counted by reason. A finding with no
kept sentence is left out. The title is a name, not a claim, and is not
checked. `report_chunk_ids` is the set the kept sentences cite.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from retrieve.citations import enforce_citations

FINDING_PREFIX = "- "


class ReplyError(ValueError):
    """The reply is not the shape the report schema asks for."""


@dataclass(frozen=True, slots=True)
class ReportReply:
    title: str
    summary: str
    findings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CheckedReport:
    title: str
    summary: str  # its kept sentences
    findings: tuple[str, ...]  # the kept sentences of each finding that kept any
    report_chunk_ids: tuple[str, ...]  # sorted
    kept_sentences: int
    dropped: int
    dropped_by_reason: Mapping[str, int]

    @property
    def text(self) -> str:
        """The kept summary, then one line per kept finding."""
        lines = [self.summary] if self.summary else []
        return "\n".join([*lines, *(FINDING_PREFIX + f for f in self.findings)])


EMPTY = CheckedReport("", "", (), (), 0, 0, {})


def check_report(reply: ReportReply, evidence: Collection[str]) -> CheckedReport:
    """*reply* with every sentence whose citations are not all in *evidence* dropped."""
    checks = [enforce_citations(text, evidence) for text in (reply.summary, *reply.findings)]
    reasons = Counter(d.reason for c in checks for d in c.dropped)
    return CheckedReport(
        title=reply.title,
        summary=checks[0].text,
        findings=tuple(c.text for c in checks[1:] if c.kept),
        report_chunk_ids=tuple(sorted({cid for c in checks for s in c.kept
                                       for cid in s.citations})),
        kept_sentences=sum(len(c.kept) for c in checks),
        dropped=sum(reasons.values()),
        dropped_by_reason=dict(sorted(reasons.items())),
    )


def parse_report_reply(text: str) -> ReportReply:
    """The reply's fields; ReplyError when it is not the schema's shape."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ReplyError("the reply is not valid JSON") from None
    if not isinstance(data, dict) or set(data) != {"title", "summary", "findings"}:
        raise ReplyError("the reply does not have exactly the schema's fields")
    title, summary, findings = data["title"], data["summary"], data["findings"]
    if not (isinstance(title, str) and isinstance(summary, str) and isinstance(findings, list)):
        raise ReplyError("title and summary must be strings and findings a list")
    if not all(isinstance(f, dict) and set(f) == {"text"} and isinstance(f["text"], str)
               for f in findings):
        raise ReplyError("each finding must be an object with one string field, text")
    return ReportReply(title, summary, tuple(f["text"] for f in findings))
