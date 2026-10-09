"""What the review command prints: triples, chunks and the round summary (Step 8).

The evidence span is marked with >>> and <<< in the chunk text, at the place
the span check matched it (extract.spans), so the reviewer reads the quote in
its context.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from extract.review_sample import Candidates, EvidenceEntry, NodeProperties, ReviewEdge
from extract.spans import find_span

Out = Callable[..., None]
RULE = "-" * 72
MARK_START = ">>>"
MARK_END = "<<<"
KEY = "key"
NAME_FIELDS = ("name", "title", "concept")  # the first a node has is shown as its name
INDENT = "  "
LABEL_WIDTH = 12  # "properties: " and the other field labels line up at this width


def highlight(text: str, span: str) -> str | None:
    """*text* with the part *span* quotes marked, or None when the span is not in it."""
    exact = find_span(span, text)
    at = text.find(exact) if exact else -1
    if exact is None or at < 0:
        return None
    return f"{text[:at]}{MARK_START}{exact}{MARK_END}{text[at + len(exact):]}"


def _name_field(properties: NodeProperties) -> str | None:
    """The property shown as the node's name: the first of NAME_FIELDS it holds as text."""
    return next((f for f in NAME_FIELDS if isinstance(properties.get(f), str)), None)


def _node(item: str, nodes: Mapping[str, NodeProperties]) -> str:
    """The node's name (when the run holds the node) and its item."""
    properties = nodes.get(item, {})
    field = _name_field(properties)
    return f'"{properties[field]}"  {item}' if field else item


def _node_details(item: str, nodes: Mapping[str, NodeProperties]) -> str:
    """The node's properties besides its key and name, e.g. value=3.7, unit='percent'."""
    properties = nodes.get(item, {})
    hidden = (KEY, _name_field(properties))
    return ", ".join(f"{k}={v!r}" for k, v in sorted(properties.items()) if k not in hidden)


def _field(label: str, value: str) -> str:
    return f"{INDENT}{label:<{LABEL_WIDTH}}{value}"


def _endpoint(out: Out, label: str, item: str, nodes: Mapping[str, NodeProperties]) -> None:
    """One endpoint line, and under it the node's other properties when it has any."""
    out(_field(label, _node(item, nodes)))
    if details := _node_details(item, nodes):
        out(_field("", details))


def _flags(candidates: Candidates, edge: ReviewEdge, chunk_id: str) -> list[str]:
    items = {edge.item: "edge", edge.start_item: "start", edge.end_item: "end"}
    return [f"{reason} ({items[item]})" for item, chunk, reason in candidates.flags
            if item in items and chunk == chunk_id]


def show_triple(out: Out, candidates: Candidates, edge: ReviewEdge, evidence: EvidenceEntry,
                text: str, position: str) -> None:
    out(RULE)
    out(f"{position}  {edge.type}")
    _endpoint(out, "start:", edge.start_item, candidates.nodes)
    _endpoint(out, "end:", edge.end_item, candidates.nodes)
    shown = ", ".join(f"{k}={v!r}" for k, v in sorted(edge.properties.items()))
    out(_field("properties:", shown or "(none)"))
    out(_field("confidence:", f"{evidence.confidence}   (uncalibrated)"))
    out(_field("flags:", ", ".join(_flags(candidates, edge, evidence.chunk_id)) or "(none)"))
    others = len(edge.evidence) - 1
    out(_field("chunk:", evidence.chunk_id)
        + (f"   (also read from {others} other chunk{'s' * (others > 1)})" if others else ""))
    out(_field("span:", evidence.span))
    out(RULE)
    marked = highlight(text, evidence.span)
    if marked is None:
        out("(the span was not found in this chunk's text; judge it wrong)")
    out(marked or text)
    out(RULE)


def show_chunk(out: Out, candidates: Candidates, chunk_id: str, text: str,
               position: str) -> None:
    """A whole chunk, then every triple read from it."""
    out(RULE)
    out(f"{position}  {chunk_id}")
    out(RULE)
    out(text)
    out(RULE)
    found = [(e, ev) for e in candidates.edges for ev in e.evidence if ev.chunk_id == chunk_id]
    if not found:
        out("No triples were extracted from this chunk.")
    for edge, ev in found:
        out(f"- {edge.type}  ({ev.confidence})")
        _endpoint(out, "start:", edge.start_item, candidates.nodes)
        _endpoint(out, "end:", edge.end_item, candidates.nodes)
        out(_field("span:", ev.span))
    out(RULE)


def _percent(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.1%}"


def show_summary(out: Out, summary: Mapping[str, object]) -> None:
    out(f"Round {summary['round']}: {summary['correct']} of {summary['judged']} correct "
        f"({summary['skipped']} skipped), accuracy {_percent(summary['accuracy'])}")
    interval = summary["wilson_95"]
    if isinstance(interval, Sequence):
        out(f"  Wilson 95% interval: {_percent(interval[0])} to {_percent(interval[1])}")
    levels = summary["by_confidence"]
    if isinstance(levels, Mapping):
        for level, figures in levels.items():
            out(f"  {level} (uncalibrated): {figures['correct']} of {figures['judged']}")
    median = summary["median_seconds"]
    out(f"  median time per answer: {'n/a' if median is None else f'{median:.0f} s'}; "
        f"{summary['fast_answers']} answers under {summary['fast_seconds']:.0f} s")
    miss = summary["miss_check"]
    if isinstance(miss, Mapping):
        out(f"  miss check (directional): {miss['missing']} of {miss['chunks']} chunks "
            "missing something important")
