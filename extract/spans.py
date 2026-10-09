"""The evidence span check (Step 8).

A span the model quotes must appear in the chunk's text from resolve(). Both
sides are normalized character by character: NFKC, curly quotes and en and em
dashes mapped to ASCII, markdown escapes dropped (a backslash before ASCII
punctuation, as the parsed filings print "Co\\., Ltd\\."; user decision
2026-10-09), and whitespace runs collapsed to one space. The match is then a
case-sensitive substring test, with no fuzzy matching. A match returns the
chunk's own text for the span, so stored evidence is always a real slice of
the filing.
"""
from __future__ import annotations

import string
import unicodedata

_TO_ASCII = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-",
})
_ESCAPABLE = frozenset(string.punctuation)


def _normalized(text: str) -> tuple[str, list[int]]:
    """*text* normalized, and for each output character the index it came from."""
    out: list[str] = []
    origin: list[int] = []
    for i, ch in _unescaped(text):
        for c in unicodedata.normalize("NFKC", ch).translate(_TO_ASCII):
            if c.isspace():
                if not out or out[-1] == " ":
                    continue
                c = " "
            out.append(c)
            origin.append(i)
    if out and out[-1] == " ":
        out.pop()
        origin.pop()
    return "".join(out), origin


def _unescaped(text: str) -> list[tuple[int, str]]:
    """Each character of *text* that markdown would print, with its index.

    A backslash before ASCII punctuation escapes it and is dropped; an escaped
    backslash prints as one backslash and escapes nothing further.
    """
    kept: list[tuple[int, str]] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in _ESCAPABLE:
            i += 1
        kept.append((i, text[i]))
        i += 1
    return kept


def unescape(text: str) -> str:
    """*text* with markdown escapes dropped: "Co\\., Ltd\\." becomes "Co., Ltd."."""
    return "".join(ch for _, ch in _unescaped(text))


def normalize(text: str) -> str:
    """*text* as the span check compares it."""
    return _normalized(text)[0]


def find_span(span: str, text: str) -> str | None:
    """The part of *text* that *span* quotes, or None when *span* is blank or not in it."""
    needle = normalize(span)
    if not needle:
        return None
    haystack, origin = _normalized(text)
    start = haystack.find(needle)
    if start < 0:
        return None
    return text[origin[start]:origin[start + len(needle) - 1] + 1]
