"""Risk factor body evidence (Step 11; ADR-0004 amendment of 2026-10-14).

A risk factor's body is its text from the end of its heading to the next risk
factor's heading, or to the end of the filing's risk-factor section. Every
chunk the body touches becomes evidence for the factor, so a question about a
factor's content reaches the chunk that holds it, not only the heading's.

- The heading is where the factor's extracted evidence span appears last in
  the section, matched with the Step 8 span normalization. A factor with
  several spans uses its most confident ones (the builder's choice) and, of
  those, the one appearing latest. That appearance is a heading only when it
  starts a line, after nothing but whitespace and a markdown bold or heading
  marker (Step 11 decision, q0127): the extractor also makes risk factors from
  sentences inside a body, and those neither get a body nor end one. Taking the
  last appearance skips the "Risk Factors Summary" list, which repeats the
  headings before the bodies.
- The next heading is the nearest other factor's heading, found the same way,
  that starts at or after this heading's end.
- Each body entry quotes the first SPAN_CHARS characters of the body inside its
  chunk, verbatim, after leading whitespace and markdown bold markers, with
  confidence `stated` and extract prompt BODY_PROMPT.
- A factor whose span is not in the section gets no body and is listed as
  unlocated; one found only mid-line is listed as not a heading. A body over
  LONG_BODY_CHUNKS chunks is listed, never cut.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from extract.spans import last_span_offsets, normalized_text
from graph.batch import Evidence
from graph.build_input import RISK_SECTION_BY_FORM, ChunkRow, RiskSection, StructuralRows
from graph.ontology import Confidence

BODY_PROMPT = "body/v1"
SPAN_CHARS = 200
LONG_BODY_CHUNKS = 5
_LEADING = " \t\r\n*"  # whitespace and the bold markers that close a heading
_BEFORE_HEADING = re.compile(r"[ \t]*(?:\*{1,3}|#{1,6} )?[ \t]*")  # line start to heading


@dataclass(frozen=True, slots=True)
class Factor:
    """A risk factor to find a body for: its key, its filing and its extracted spans."""

    key: str
    accession_no: str
    spans: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Bodies:
    evidence: Mapping[str, tuple[Evidence, ...]]  # factor key -> body entries, in chunk order
    unlocated: tuple[str, ...]  # factor keys, sorted
    not_headings: tuple[str, ...]  # factor keys found only mid-line, sorted
    long_bodies: tuple[tuple[str, int], ...]  # (factor key, chunks), sorted by key


def body_evidence(rows: StructuralRows, factors: Iterable[Factor]) -> Bodies:
    """Body entries for every factor whose heading is in its filing's risk-factor section."""
    sections = {s.accession_no: s for s in rows.risk_sections}
    forms = {f.accession_no: f.form_type for f in rows.filings}
    by_filing: dict[str, list[Factor]] = {}
    for factor in factors:
        by_filing.setdefault(factor.accession_no, []).append(factor)
    evidence: dict[str, tuple[Evidence, ...]] = {}
    unlocated: list[str] = []
    not_headings: list[str] = []
    for accession_no, found in by_filing.items():
        section = sections.get(accession_no)
        if section is None:
            unlocated.extend(f.key for f in found)
            continue
        located = _located(section, found)
        headings = {k: v for k, v in located.items() if _starts_line(section.text, v[0])}
        unlocated.extend(f.key for f in found if f.key not in located)
        not_headings.extend(k for k in located if k not in headings)
        if headings:
            name = RISK_SECTION_BY_FORM.get(forms.get(accession_no, ""))
            chunks = sorted((c for c in rows.chunks
                             if c.accession_no == accession_no and c.section == name),
                            key=lambda c: c.char_start)
            evidence.update(_bodies(section, headings, chunks))
    long_bodies = tuple(sorted((key, len(entries)) for key, entries in evidence.items()
                               if len(entries) > LONG_BODY_CHUNKS))
    return Bodies(evidence, tuple(sorted(unlocated)), tuple(sorted(not_headings)), long_bodies)


def _located(section: RiskSection, factors: Sequence[Factor]) -> dict[str, tuple[int, int]]:
    """Factor key -> its span's last [start, end) in the section text, for those found."""
    normalized = normalized_text(section.text)
    found: dict[str, tuple[int, int]] = {}
    for factor in factors:
        matches = [m for span in factor.spans if (m := last_span_offsets(span, normalized))]
        if matches:
            found[factor.key] = max(matches)
    return found


def _starts_line(text: str, start: int) -> bool:
    return _BEFORE_HEADING.fullmatch(text, text.rfind("\n", 0, start) + 1, start) is not None


def _bodies(
    section: RiskSection, headings: Mapping[str, tuple[int, int]], chunks: Sequence[ChunkRow],
) -> dict[str, tuple[Evidence, ...]]:
    starts = sorted(start for start, _ in headings.values())
    bodies = {}
    for key, (_, heading_end) in headings.items():
        body_end = next((s for s in starts if s >= heading_end), len(section.text))
        bodies[key] = tuple(_entries(section, heading_end, body_end, chunks))
    return bodies


def _entries(
    section: RiskSection, start: int, end: int, chunks: Sequence[ChunkRow],
) -> Iterable[Evidence]:
    """One entry per chunk the section text [start, end) touches with any printed text."""
    for chunk in chunks:
        lo = max(start, chunk.char_start - section.char_start)
        hi = min(end, chunk.char_end - section.char_start)
        quote = section.text[lo:hi].lstrip(_LEADING)[:SPAN_CHARS].rstrip()
        if quote:
            yield Evidence(chunk.chunk_id, quote, Confidence.STATED.value, BODY_PROMPT)
