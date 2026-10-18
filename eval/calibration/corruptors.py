"""Four known errors planted in a real answer, by code with no model call (Step 13 ticket 04).

From one answer's kept sentences and retrieved chunks, each corruptor makes one
copy with exactly one change, in the first place it applies:

- ``changed_number``: the first number with a unit (``$``, ``%``, ``percent``,
  ``billion``, ``million``, ``thousand``, ``basis points``) is multiplied by
  ``NUMBER_FACTOR``; a value a retrieved chunk already prints is passed over;
- ``swapped_period``: the first fiscal period ("fiscal 2025", "fiscal year 2025",
  "FY2025", or "in/for/during 2025") gets the corpus's neighbouring fiscal year;
- ``wrong_citation``: the first sentence citing exactly one chunk cites instead
  the lowest-ranked retrieved chunk the answer never cites;
- ``added_sentence``: a sentence stating a fact no retrieved chunk mentions is
  appended, citing the last sentence's first citation.

Where a corruptor cannot apply it returns ``Skipped`` with the reason, so skips
are counted.  Nothing here is random: the corruption test's seed only draws the
answers.  Citations stay inside the retrieved chunks, so every copy still passes
citation enforcement and only the judge can catch the error.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Literal

from eval.calibration.answers import RunAnswer
from retrieve.citations import Sentence

CorruptionKind = Literal["changed_number", "swapped_period", "wrong_citation",
                         "added_sentence"]
KINDS: tuple[CorruptionKind, ...] = ("changed_number", "swapped_period", "wrong_citation",
                                     "added_sentence")
NUMBER_FACTOR = 1.5
# The fiscal years the 24 corpus filings cover (tests/fixtures/corpus_filings.json).
CORPUS_FISCAL_YEARS = (2024, 2025, 2026)
ADDED_FACT_MARKER = "Reykjavik"
ADDED_SENTENCE = ("The filing also states that the company opened a research campus in "
                  f"{ADDED_FACT_MARKER}, Iceland, that employs 4,200 people [{{chunk_id}}].")

_UNIT_NUMBER = re.compile(
    r"(?P<pre>\$\s?)?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"(?P<post>\s?(?:%|percent\b|billion\b|million\b|thousand\b|basis points\b))?")
_PERIOD = re.compile(r"\b(?P<lead>fiscal year |fiscal |FY ?|in |for |during )(?P<year>20\d\d)\b"
                     r"(?![-/\d])")
_CITATION = re.compile(r"\[[^\[\]\n]*\]")


@dataclass(frozen=True)
class Corruption:
    kind: CorruptionKind
    sentence_index: int  # the changed or added sentence, in ``kept``
    kept: tuple[Sentence, ...]  # the corrupted answer's kept sentences
    before: str  # the text replaced ("" for an added sentence)
    after: str  # the text put in its place, or the added sentence


@dataclass(frozen=True)
class Skipped:
    kind: CorruptionKind
    reason: str


Outcome = Corruption | Skipped


def _outside_citations(text: str, start: int, end: int) -> bool:
    return not any(m.start() < end and start < m.end() for m in _CITATION.finditer(text))


def _scaled(match: re.Match[str]) -> str | None:
    digits, decimals = match["int"].replace(",", ""), match["dec"] or ""
    value = float(digits + decimals)
    if value == 0:
        return None
    scaled = value * NUMBER_FACTOR
    places = len(decimals) - 1 if decimals else (0 if scaled.is_integer() else 1)
    number = f"{scaled:,.{places}f}" if "," in match["int"] else f"{scaled:.{places}f}"
    return f"{match['pre'] or ''}{number}{match['post'] or ''}"


def _number_matches(text: str) -> Iterator[re.Match[str]]:
    for match in _UNIT_NUMBER.finditer(text):
        if (match["pre"] or match["post"]) and _outside_citations(text, *match.span()):
            yield match


def _replace_in(answer: RunAnswer, index: int, start: int, end: int, new: str
                ) -> tuple[Sentence, ...]:
    sentence = answer.kept[index]
    text = sentence.text[:start] + new + sentence.text[end:]
    return (*answer.kept[:index], replace(sentence, text=text), *answer.kept[index + 1:])


def _changed_number(answer: RunAnswer, texts: Mapping[str, str]) -> Outcome:
    shown = [texts.get(c, "") for c in answer.retrieved]
    for index, sentence in enumerate(answer.kept):
        for match in _number_matches(sentence.text):
            after = _scaled(match)
            if after is None or any(after.lstrip("$ ") in t for t in shown):
                continue
            return Corruption("changed_number", index,
                              _replace_in(answer, index, *match.span(), after),
                              match[0], after)
    return Skipped("changed_number", "no number with a unit that can be changed")


def _other_year(year: int) -> int | None:
    if year not in CORPUS_FISCAL_YEARS:
        return None
    return year - 1 if year - 1 in CORPUS_FISCAL_YEARS else year + 1


def _swapped_period(answer: RunAnswer, texts: Mapping[str, str]) -> Outcome:
    for index, sentence in enumerate(answer.kept):
        for match in _PERIOD.finditer(sentence.text):
            other = _other_year(int(match["year"]))
            if other is None or not _outside_citations(sentence.text, *match.span()):
                continue
            after = f"{match['lead']}{other}"
            return Corruption("swapped_period", index,
                              _replace_in(answer, index, *match.span(), after),
                              match[0], after)
    return Skipped("swapped_period", "no fiscal period of the corpus")


def _wrong_citation(answer: RunAnswer, texts: Mapping[str, str]) -> Outcome:
    cited = {c for s in answer.kept for c in s.citations}
    uncited = [c for c in answer.retrieved if c not in cited]
    if not uncited:
        return Skipped("wrong_citation", "every retrieved chunk is cited; no uncited chunk")
    index = next((i for i, s in enumerate(answer.kept) if len(s.citations) == 1), None)
    if index is None:
        return Skipped("wrong_citation", "no sentence cites exactly one chunk")
    sentence, wrong = answer.kept[index], uncited[-1]
    (old,) = sentence.citations
    kept = (*answer.kept[:index],
            replace(sentence, text=sentence.text.replace(old, wrong), citations=(wrong,)),
            *answer.kept[index + 1:])
    return Corruption("wrong_citation", index, kept, old, wrong)


def _added_sentence(answer: RunAnswer, texts: Mapping[str, str]) -> Outcome:
    marker = ADDED_FACT_MARKER.casefold()
    if any(marker in texts.get(c, "").casefold() for c in answer.retrieved):
        return Skipped("added_sentence", f"a retrieved chunk mentions {ADDED_FACT_MARKER}")
    chunk_id = answer.kept[-1].citations[0]
    added = Sentence(ADDED_SENTENCE.format(chunk_id=chunk_id), (chunk_id,))
    return Corruption("added_sentence", len(answer.kept), (*answer.kept, added), "",
                      added.text)


_CORRUPTORS: Mapping[CorruptionKind, Callable[[RunAnswer, Mapping[str, str]], Outcome]] = {
    "changed_number": _changed_number,
    "swapped_period": _swapped_period,
    "wrong_citation": _wrong_citation,
    "added_sentence": _added_sentence,
}


def corrupt(answer: RunAnswer, texts: Mapping[str, str]) -> tuple[Outcome, ...]:
    """The four corrupted copies of *answer*, in ``KINDS`` order; *texts* maps chunk IDs
    to their text, for the checks that a planted value is absent from the chunks."""
    if not answer.kept:
        raise ValueError(f"{answer.key} has no kept sentence to corrupt")
    return tuple(_CORRUPTORS[kind](answer, texts) for kind in KINDS)
