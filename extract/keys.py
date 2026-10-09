"""Node keys for extracted labels, built in code, never by the model (Step 8).

- cross-filing labels: `{label}:{norm(name)}`;
- RiskFactor: `{accession_no}:{norm(title)}`;
- MetricValue: `{accession_no}:{subject key}:{norm(concept)}:{period}`, where the
  subject key is the filer's CIK or the built key of a Segment or Product.

`norm` is NFKC, casefold and collapsed whitespace; stripping legal suffixes is
Step 9's. The period is kept as written (the prompt asks for FY2026 or FY2026-Q3).
Every name and the period have `%` and `:` percent-encoded, so a name
holding the separator cannot make two keys collide. A subject key keeps its own
separators: it sits between the accession number, which has none, and two
escaped parts, so the key still reads one way.
"""
from __future__ import annotations

import unicodedata

SEPARATOR = ":"
PER_FILING_LABEL = "RiskFactor"


def norm(text: str) -> str:
    """NFKC, then casefold, then whitespace runs collapsed to one space and trimmed."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _escaped(text: str) -> str:
    return text.replace("%", "%25").replace(SEPARATOR, "%3a")


def _part(text: str) -> str:
    return _escaped(norm(text))


def node_key(label: str, name: str, accession_no: str) -> str:
    """The key of an extracted node other than a MetricValue."""
    prefix = accession_no if label == PER_FILING_LABEL else label
    return f"{prefix}{SEPARATOR}{_part(name)}"


def metric_key(accession_no: str, subject_key: str, concept: str, period: str) -> str:
    """The key of a MetricValue: one figure for one subject and period in one filing."""
    return SEPARATOR.join((accession_no, subject_key, _part(concept), _escaped(period.strip())))
