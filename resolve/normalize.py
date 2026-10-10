"""Match keys for entity resolution (Step 9).

A match key is what two names are compared by; it is never stored as a name,
and extracted keys are never rewritten (ADR-0006). It is NFKC, casefold, with
markdown escapes, periods and apostrophes dropped ("N.A." is "na", "U.K." is
"uk"), `&` read as "and", other punctuation as a space, whitespace collapsed,
and then the legal suffixes below stripped repeatedly from the end. Stripping
stops before the last word, so "Company" alone keeps its name (cleanco empties
it; research note, section 5).
"""
from __future__ import annotations

import re
import unicodedata

from extract.spans import unescape

# Hand-written: legal-form words as they appear after the punctuation rules above.
LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "llc", "llp", "lp", "plc", "na", "sa", "ag", "se", "gmbh", "bv", "nv", "kk", "ab",
    "spa", "srl", "sarl", "pte", "pty",
})
_DROPPED = re.compile(r"[.'’‘]")
_AND = re.compile(r"&")
_NON_WORD = re.compile(r"[^\w]+|_")


def words(name: str) -> tuple[str, ...]:
    """The words of *name* before suffix stripping."""
    text = unicodedata.normalize("NFKC", unescape(name)).casefold()
    text = _AND.sub(" and ", _DROPPED.sub("", text))
    return tuple(_NON_WORD.sub(" ", text).split())


def strip_suffixes(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """*tokens* with legal suffixes removed from the end, always keeping the first word."""
    end = len(tokens)
    while end > 1 and tokens[end - 1] in LEGAL_SUFFIXES:
        end -= 1
    return tokens[:end]


def match_key(name: str) -> str:
    """The form two names of one label are compared by."""
    return " ".join(strip_suffixes(words(name)))
