"""Linking a question to anchors, the graph nodes graph_local starts from (Step 11).

Each extracted node is embedded once as text (``node_text``): "Label: name" for
the labels that hold one node across filings, "RiskFactor: title" and
"MetricValue: concept, period".  The question's query embedding is compared
with every node's by exact cosine (ADR-0002: no index).

Anchors, best first, up to ``ANCHOR_LIMIT``:
1. nodes whose name or an alias matches a phrase of the question at
   ``NAME_MATCH_CUTOFF`` or above, by match score, then cosine, then key; they
   count as similarity 1.0;
2. the other nodes by cosine, then key.

Only nodes with a name match by name: risk factors and metric values have
titles and concepts, which are not names.  Filers are Company nodes, which are
never anchors.  A node qualifies only when one of its evidence chunks lies in
the filings the question filter selected (any filing with no filter).
"""
from __future__ import annotations

import math
import operator
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.utils import default_process

from retrieve.arm import Anchor

ANCHOR_LIMIT = 20  # as GraphRAG local search (docs/research/text-to-cypher-and-local-search.md)
# fuzz.ratio between a processed name and a question phrase of about its length.
# Chosen in Step 11 ticket 05 (BUILD-LOG); spelling variants pass, other words do not.
NAME_MATCH_CUTOFF = 90.0
_PHRASE_SLACK = 1  # a phrase may have one word more or fewer than the name
RISK_FACTOR = "RiskFactor"
METRIC_VALUE = "MetricValue"


@dataclass(frozen=True)
class AnchorNode:
    """An extracted node an anchor can be, as anchor linking needs it."""

    key: str
    label: str
    text: str  # what the node is embedded as
    names: tuple[str, ...]  # its name and aliases; empty for risk factors and metric values
    filings: frozenset[str]  # accession numbers of its EVIDENCED_BY chunks


def node_text(label: str, properties: Mapping[str, object]) -> str:
    """The text a node is embedded as; raises ``ValueError`` when its property is missing."""
    if label == RISK_FACTOR:
        return f"{label}: {_text(properties, 'title')}"
    if label == METRIC_VALUE:
        return f"{label}: {_text(properties, 'concept')}, {_text(properties, 'period')}"
    return f"{label}: {_text(properties, 'name')}"


def node_names(label: str, properties: Mapping[str, object]) -> tuple[str, ...]:
    """The name and aliases a node is matched by; none for risk factors and metric values."""
    if label in (RISK_FACTOR, METRIC_VALUE):
        return ()
    aliases = properties.get("aliases") or ()
    return (_text(properties, "name"), *(str(a) for a in aliases))


def _text(properties: Mapping[str, object], name: str) -> str:
    value = properties.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"node {properties.get('key')!r} has no {name}")
    return value


class AnchorIndex:
    """Every anchor candidate with its unit vector, for exact cosine and name matching."""

    def __init__(self, nodes: Sequence[AnchorNode], vectors: Mapping[str, Sequence[float]]):
        missing = sorted({n.text for n in nodes} - set(vectors))
        if missing:
            raise ValueError(f"{len(missing)} node texts have no vector, e.g. {missing[0]!r}")
        self._nodes = tuple(nodes)
        self._units = {text: _unit(vectors[text]) for text in {n.text for n in nodes}}
        self._names = {n.key: _processed_names(n.names) for n in nodes if n.names}

    def __len__(self) -> int:
        return len(self._nodes)

    def link(self, question: str, query_vector: Sequence[float],
             accession_nos: Collection[str] | None, limit: int = ANCHOR_LIMIT,
             cutoff: float = NAME_MATCH_CUTOFF) -> tuple[Anchor, ...]:
        """The question's anchors, best first; empty when no node qualifies."""
        allowed = None if accession_nos is None else frozenset(accession_nos)
        nodes = [n for n in self._nodes if allowed is None or n.filings & allowed]
        query = _unit(query_vector)
        cosine = {text: _dot(query, unit) for text, unit in self._units.items()}
        phrases = _phrases(question)
        named = {n.key: score for n in nodes
                 if (score := self._name_score(n.key, phrases)) >= cutoff}
        by_name = sorted((n for n in nodes if n.key in named),
                         key=lambda n: (-named[n.key], -cosine[n.text], n.key))
        by_embedding = sorted((n for n in nodes if n.key not in named),
                              key=lambda n: (-cosine[n.text], n.key))
        anchors = [Anchor(n.key, n.label, 1.0, "name") for n in by_name]
        anchors += [Anchor(n.key, n.label, cosine[n.text], "embedding") for n in by_embedding]
        return tuple(anchors[:limit])

    def _name_score(self, key: str, phrases: Mapping[int, Sequence[str]]) -> float:
        """The best fuzz.ratio of any of the node's names against a phrase of similar length."""
        best = 0.0
        for name, words in self._names.get(key, ()):
            for size in range(max(1, words - _PHRASE_SLACK), words + _PHRASE_SLACK + 1):
                for phrase in phrases.get(size, ()):
                    best = max(best, fuzz.ratio(name, phrase))
        return best


def _processed_names(names: Sequence[str]) -> tuple[tuple[str, int], ...]:
    processed = (default_process(name) for name in names)
    return tuple((p, len(p.split())) for p in dict.fromkeys(processed) if p)


def _phrases(question: str) -> dict[int, tuple[str, ...]]:
    """Every run of consecutive words in the processed question, by word count."""
    words = default_process(question).split()
    return {size: tuple(" ".join(words[i:i + size]) for i in range(len(words) - size + 1))
            for size in range(1, len(words) + 1)}


def _unit(vector: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(_dot(vector, vector))
    if norm == 0.0:
        raise ValueError("cannot compare a zero vector")
    return tuple(x / norm for x in vector)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(map(operator.mul, a, b))
