"""Pure builders for what the page draws from an answer (Step 14).

Every piece of model or filing text is escaped here before it reaches
``st.html``, ``st.markdown`` or a Graphviz DOT string, so nothing a filing or a
model wrote is read as HTML, Markdown or DOT.
"""
from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from retrieve.answer import DECLINE_TEXT, NOT_FOUND_TEXT, AnswerStatus
from retrieve.arm import ChunkSupport, SupportEdge
from retrieve.citations import CitationCheck, Sentence, strip_citations
from ui.source import SourcePassage

LEAD_TEXT: Mapping[str, str] = {"declined": DECLINE_TEXT, "not_found": NOT_FOUND_TEXT}
DROP_REASONS: Mapping[str, str] = {"no_citation": "no citation",
                                   "citation_not_retrieved": "cites a chunk not retrieved"}
_PRE = ("margin:0;white-space:pre-wrap;word-break:break-word;font-size:0.85rem;"
        "line-height:1.45;padding:0.5rem 0.75rem")
_DIM = "opacity:0.55"
_MARK = "background:rgba(255,200,0,0.35);color:inherit;padding:0"
_BOX = "border:1px solid rgba(128,128,128,0.35);border-radius:0.5rem;overflow:hidden"
_CONTEXT = "max-height:10rem;overflow-y:auto"
# A column-reverse box opens scrolled to its end, so the text just before the chunk shows
# first, with no JavaScript.
_BEFORE = f"{_CONTEXT};display:flex;flex-direction:column-reverse"
_CODE_SPAN = re.compile(r"(`+)(?:.|\n)*?\1")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-!|~$<>:])")
_EDGES_PER_LINE = 3


def citation_numbers(sentences: Sequence[Sentence]) -> dict[str, int]:
    """Each cited chunk ID and its marker number, in order of first citation."""
    ids = dict.fromkeys(cid for s in sentences for cid in s.citations)
    return {cid: n for n, cid in enumerate(ids, start=1)}


def answer_html(status: AnswerStatus, sentences: Sequence[Sentence],
                numbers: Mapping[str, int]) -> str:
    """The answer as a reader sees it: the fixed sentence of a decline or not-found
    answer, then each kept sentence escaped, its citations as numbered markers."""
    parts = [f"<p><em>{html.escape(LEAD_TEXT[status])}</em></p>"] if status in LEAD_TEXT else []
    for sentence in sentences:
        markers = "".join(f"<sup>[{numbers[cid]}]</sup>" for cid in sentence.citations)
        parts.append(f"<p>{html.escape(strip_citations(sentence.text))}{markers}</p>")
    return f'<div class="fi-answer">{"".join(parts)}</div>'


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def dropped_line(check: CitationCheck) -> str:
    """How many sentences the citation check kept and dropped, and why (invariant 3)."""
    line = f"{plural(len(check.kept), 'sentence')} kept, " \
           f"{plural(len(check.dropped), 'sentence')} dropped"
    reasons = Counter(d.reason for d in check.dropped)
    if not reasons:
        return line + "."
    why = "; ".join(f"{DROP_REASONS[r]}: {n}" for r, n in sorted(reasons.items()))
    return f"{line} ({why})."


def passage_html(passage: SourcePassage) -> str:
    """The chunk highlighted between its dimmed filing text, all escaped preformatted text.

    The text before and after sit in short scrolling boxes, the one before opened at
    its end, so the chunk shows at once with its neighbouring lines around it."""
    def pre(text: str, style: str = "") -> str:
        return f'<pre style="{_PRE};{style}">{text}</pre>'

    before = pre(html.escape(passage.before), _DIM)
    chunk = pre(f'<mark style="{_MARK}">{html.escape(passage.chunk)}</mark>')
    after = pre(html.escape(passage.after), _DIM)
    return (f'<div class="fi-passage" style="{_BOX}">'
            f'<div style="{_BEFORE}">{before}</div>{chunk}'
            f'<div style="{_CONTEXT}">{after}</div></div>')


def markdown_dollars(text: str) -> str:
    """*text* with each ``$`` outside code spans escaped, so Streamlit's Markdown does not
    read two prices on a line as a LaTeX formula."""
    out, last = [], 0
    for match in _CODE_SPAN.finditer(text):
        out.append(text[last:match.start()].replace("$", r"\$"))
        out.append(match.group(0))
        last = match.end()
    out.append(text[last:].replace("$", r"\$"))
    return "".join(out)


def md_escape(text: str) -> str:
    """*text* with every character Streamlit's Markdown would act on backslash-escaped,
    and line breaks folded, so model or filing text shows as written."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def dot_string(text: str) -> str:
    """*text* as the inside of a quoted DOT string: backslashes and quotes escaped, and
    line breaks folded, so no label can end its string or add a DOT escape."""
    return " ".join(text.split()).replace("\\", "\\\\").replace('"', '\\"')


def support_dot(support: Sequence[ChunkSupport], numbers: Mapping[str, int]) -> str:
    """graph_local's support as a DOT drawing: each anchor, each kept chunk, and a line
    for the anchor's own evidence and for its edges citing the chunk."""
    anchors = list(dict.fromkeys((s.anchor_label, s.anchor_key) for s in support))
    chunks = list(dict.fromkeys(s.chunk_id for s in support))
    lines = ["digraph trace {", "  rankdir=LR;",
             '  node [shape=box, fontsize=10, fontname="Helvetica"];',
             '  edge [fontsize=9, fontname="Helvetica"];']
    lines += [f'  a{i} [shape=ellipse, label="{dot_string(label)}\\n{dot_string(key)}"];'
              for i, (label, key) in enumerate(anchors)]
    lines += [f'  c{i} [label="{dot_string(_chunk_label(cid, numbers))}"];'
              for i, cid in enumerate(chunks)]
    for item in support:
        start = f"a{anchors.index((item.anchor_label, item.anchor_key))}"
        end = f"c{chunks.index(item.chunk_id)}"
        for label in _support_labels(item):
            lines.append(f'  {start} -> {end} [label="{dot_string(label)}"];')
    lines.append("}")
    return "\n".join(lines)


def _chunk_label(chunk_id: str, numbers: Mapping[str, int]) -> str:
    return f"[{numbers[chunk_id]}] {chunk_id}" if chunk_id in numbers else chunk_id


def _support_labels(item: ChunkSupport) -> list[str]:
    labels = ["EVIDENCED_BY"] if item.evidenced else []
    edges = [*item.edges, *item.neighbours]
    labels += [_edge_label(e) for e in edges[:_EDGES_PER_LINE]]
    if len(edges) > _EDGES_PER_LINE:
        labels.append(f"and {len(edges) - _EDGES_PER_LINE} more edges")
    return labels or ["score share"]


def _edge_label(edge: SupportEdge) -> str:
    arrow = "->" if edge.outgoing else "<-"
    return f"{edge.type} {arrow} {edge.other_label}: {edge.other_name or edge.other_id}"
