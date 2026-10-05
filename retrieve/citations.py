"""Citation enforcement for generated answers (Step 5, invariant 3).

An answer is split into sentences deterministically.  A sentence survives only
if it cites at least one ``[chunk_id]`` and every chunk it cites was retrieved
for this question.  Dropped sentences are kept with their reason, so a reader
can see what was removed and why.

Splitting rules: each line is split on its own; a sentence ends at ``.``, ``!``
or ``?`` followed by whitespace or the end of the line, and any citations
straight after the punctuation belong to the sentence before it.  A full stop
after a common abbreviation (``Inc.``, ``vs.``) or initials (``U.S.``, ``i.e.``)
does not end a sentence, and a decimal point never does.  A piece holding only
citations joins the sentence before it.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from functools import reduce
from typing import Literal

CHUNK_ID = re.compile(r"\d{10}-\d{2}-\d{6}:\d{4,}")
_BRACKET = re.compile(r"\[([^\[\]\n]*)\]")
_SENTENCE_END = re.compile(r"[.!?][\"'’”)]*(?:\s*\[[^\[\]\n]*\])*(?=\s|$)")
_LAST_WORD = re.compile(r"(\S+)$")
_INITIALS = re.compile(r"(?:[A-Za-z]\.)+[A-Za-z]")
_ABBREVIATIONS = frozenset({
    "inc", "corp", "co", "ltd", "llc", "plc", "no", "nos", "vs", "approx", "est", "fig",
    "mr", "ms", "mrs", "dr", "st", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec",
})
_WORD_CHAR = re.compile(r"\w")

DropReason = Literal["no_citation", "citation_not_retrieved"]


@dataclass(frozen=True)
class Sentence:
    text: str
    citations: tuple[str, ...]  # chunk IDs in order of first appearance, each once
    ends_line: bool = False


@dataclass(frozen=True)
class DroppedSentence:
    text: str
    citations: tuple[str, ...]
    reason: DropReason


@dataclass(frozen=True)
class CitationCheck:
    """An answer after enforcement, with the structural citation counts."""

    kept: tuple[Sentence, ...]
    dropped: tuple[DroppedSentence, ...]
    citations: int  # cited chunk IDs over all sentences, kept or dropped
    citations_retrieved: int  # of those, the ones among the retrieved chunks

    @property
    def sentences(self) -> int:
        return len(self.kept) + len(self.dropped)

    @property
    def text(self) -> str:
        """The kept sentences, with line breaks where the answer had them."""
        return "".join(
            s.text + ("" if i == len(self.kept) - 1 else "\n" if s.ends_line else " ")
            for i, s in enumerate(self.kept)
        )


def cited_ids(text: str) -> tuple[str, ...]:
    """Chunk IDs cited in square brackets in *text*, in order, each once."""
    found = (cid for m in _BRACKET.finditer(text) for cid in CHUNK_ID.findall(m.group(1)))
    return tuple(dict.fromkeys(found))


def _after_abbreviation(line: str, stop: int) -> bool:
    if line[stop] != ".":
        return False
    word = _LAST_WORD.search(line[:stop])
    if word is None:
        return False
    token = word.group(1).lstrip("(\"'")
    return token.lower() in _ABBREVIATIONS or _INITIALS.fullmatch(token) is not None


def _split_line(line: str) -> list[str]:
    ends = [m.end() for m in _SENTENCE_END.finditer(line)
            if not _after_abbreviation(line, m.start())]
    bounds = list(zip([0, *ends], [*ends, len(line)], strict=True))
    return [line[a:b].strip() for a, b in bounds if line[a:b].strip()]


def _pieces(text: str) -> Iterable[Sentence]:
    for line in text.splitlines():
        parts = _split_line(line)
        for i, part in enumerate(parts):
            yield Sentence(part, cited_ids(part), ends_line=i == len(parts) - 1)


def _only_citations(text: str) -> bool:
    return _WORD_CHAR.search(_BRACKET.sub("", text)) is None


def _merge(done: tuple[Sentence, ...], piece: Sentence) -> tuple[Sentence, ...]:
    """*done* plus *piece*; a piece holding only citations joins the sentence before it."""
    if not done or not _only_citations(piece.text):
        return (*done, piece)
    joined = f"{done[-1].text} {piece.text}"
    last = replace(done[-1], text=joined, citations=cited_ids(joined), ends_line=piece.ends_line)
    return (*done[:-1], last)


def split_sentences(text: str) -> tuple[Sentence, ...]:
    """The sentences of *text*, each with the chunk IDs it cites."""
    return reduce(_merge, _pieces(text), ())


def _drop_reason(sentence: Sentence, retrieved: Collection[str]) -> DropReason | None:
    if not sentence.citations:
        return "no_citation"
    if any(cid not in retrieved for cid in sentence.citations):
        return "citation_not_retrieved"
    return None


def enforce_citations(text: str, retrieved: Collection[str]) -> CitationCheck:
    """Keep the sentences of *text* whose citations are all in *retrieved*."""
    sentences = split_sentences(text)
    verdicts = [(s, _drop_reason(s, retrieved)) for s in sentences]
    return CitationCheck(
        kept=tuple(s for s, reason in verdicts if reason is None),
        dropped=tuple(
            DroppedSentence(s.text, s.citations, reason)
            for s, reason in verdicts if reason is not None
        ),
        citations=sum(len(s.citations) for s in sentences),
        citations_retrieved=sum(cid in retrieved for s in sentences for cid in s.citations),
    )
