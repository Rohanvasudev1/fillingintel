"""The extracted nodes entity resolution works on, read from candidates files (Step 9).

A candidates file (`data/extract/{accession_no}-run{N}.jsonl`, written by
`extract.run`) holds one line per merged node and edge. Cross-filing labels
share one key across filings, so a key seen in several files is one node whose
evidence is the union of theirs, its name the first file's. A MetricValue also
needs its subject, which only its `MEASURES` edge holds: the filer's CIK, or
the key of the Segment, MarketPlatform or Product it measures, as in its key.
A MetricValue whose MEASURES edge extraction dropped has no subject, so it can
never pair; it is skipped and its key listed on its file (user decision,
2026-10-11).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

METRIC_LABEL = "MetricValue"
RISK_LABEL = "RiskFactor"
_NAME_PROPERTY = {METRIC_LABEL: "concept", RISK_LABEL: "title"}
_MEASURES = "MEASURES"


class InputError(ValueError):
    """A candidates file is missing or does not hold what extraction writes."""


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    chunk_id: str
    span: str

    @property
    def accession_no(self) -> str:
        return self.chunk_id.split(":", 1)[0]


@dataclass(frozen=True, slots=True)
class MetricFacts:
    """What two MetricValues must share to be paired."""

    accession_no: str
    subject_key: str
    period: str
    value: float
    unit: str


@dataclass(frozen=True, slots=True)
class ResolveNode:
    """One extracted node: its key, label, printed name and evidence."""

    key: str
    label: str
    name: str  # the concept of a MetricValue, the title of a RiskFactor
    evidence: tuple[EvidenceRef, ...]
    metric: MetricFacts | None = None

    def __post_init__(self) -> None:
        if not self.key or not self.label:
            raise ValueError("a node needs a key and a label")
        if not self.evidence:
            raise ValueError(f"{self.key} has no evidence")
        if (self.metric is None) == (self.label == METRIC_LABEL):
            raise ValueError(f"{self.key}: metric facts belong to MetricValues only")

    @property
    def filer_ciks(self) -> tuple[str, ...]:
        """The CIKs of the filings its evidence comes from (an accession number starts with
        the filer's zero-padded CIK, as every corpus filing is filed by its own company)."""
        return tuple(sorted({str(int(e.accession_no.split("-", 1)[0])) for e in self.evidence}))

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        """Its evidence chunks, each once, in order."""
        return tuple(dict.fromkeys(e.chunk_id for e in self.evidence))


@dataclass(frozen=True, slots=True)
class InputFile:
    """A candidates file read, for the run report's provenance."""

    name: str
    sha256: str
    skipped_metrics: tuple[str, ...] = ()  # MetricValue keys with no MEASURES edge


def load_nodes(paths: Sequence[Path]) -> tuple[tuple[ResolveNode, ...], tuple[InputFile, ...]]:
    """Every node in *paths*, one per key, sorted by label and key; InputError names the line."""
    merged: dict[str, ResolveNode] = {}
    files = []
    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise InputError(f"cannot read {path.name}: {type(exc).__name__}") from exc
        found, skipped = _file_nodes(path.name, raw.decode("utf-8"))
        files.append(InputFile(path.name, hashlib.sha256(raw).hexdigest(), skipped))
        for node in found:
            merged[node.key] = _union(merged.get(node.key), node)
    return tuple(sorted(merged.values(), key=lambda n: (n.label, n.key))), tuple(files)


def _union(kept: ResolveNode | None, new: ResolveNode) -> ResolveNode:
    if kept is None:
        return new
    if kept.label != new.label or kept.metric != new.metric:
        raise InputError(f"{new.key} differs between files in label or metric facts")
    evidence = tuple(dict.fromkeys((*kept.evidence, *new.evidence)))
    return ResolveNode(kept.key, kept.label, kept.name, evidence, kept.metric)


def _file_nodes(file_name: str, text: str) -> tuple[list[ResolveNode], tuple[str, ...]]:
    """The file's nodes, and the keys of MetricValues skipped for want of a subject."""
    lines: list[tuple[int, Mapping[str, object]]] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        try:
            line = json.loads(raw_line)
        except ValueError as exc:
            raise InputError(f"{file_name} line {number} is not JSON") from exc
        if not isinstance(line, dict):
            raise InputError(f"{file_name} line {number} is not an object")
        lines.append((number, line))
    subjects = _subjects(file_name, lines)
    nodes, skipped = [], []
    for number, line in lines:
        if line.get("kind") != "node":
            continue
        orphan = _orphan_metric_key(line, subjects)
        if orphan is not None:
            skipped.append(orphan)
            continue
        try:
            nodes.append(_node(line, subjects))
        except (KeyError, TypeError, ValueError) as exc:
            raise InputError(f"{file_name} line {number} is not a node line: {exc}") from exc
    return nodes, tuple(skipped)


def _orphan_metric_key(line: Mapping[str, object], subjects: Mapping[str, str]) -> str | None:
    """The key of a MetricValue line with no MEASURES edge, else None."""
    properties = line.get("properties")
    if line.get("label") != METRIC_LABEL or not isinstance(properties, Mapping):
        return None
    key = properties.get("key")
    return key if isinstance(key, str) and key not in subjects else None


def _subjects(file_name: str, lines: Iterable[tuple[int, Mapping[str, object]]]
              ) -> dict[str, str]:
    """MetricValue key to its subject key, from the MEASURES edges."""
    subjects: dict[str, str] = {}
    for number, line in lines:
        if line.get("kind") != "edge" or line.get("type") != _MEASURES:
            continue
        try:
            start, end = line["start"]["key"]["key"], line["end"]
            subject = end["key"]["cik"] if end["label"] == "Company" else end["key"]["key"]
        except (KeyError, TypeError) as exc:
            raise InputError(f"{file_name} line {number} is not a MEASURES edge") from exc
        if start in subjects:
            raise InputError(f"{file_name}: {start} has two MEASURES edges")
        subjects[_text(start, "metric key")] = _text(subject, "subject key")
    return subjects


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{what} is not a non-blank string")
    return value


def _node(line: Mapping[str, object], subjects: Mapping[str, str]) -> ResolveNode:
    label = _text(line["label"], "label")
    properties = line["properties"]
    if not isinstance(properties, Mapping):
        raise TypeError("properties is not an object")
    key = _text(properties["key"], "key")
    name = _text(properties[_NAME_PROPERTY.get(label, "name")], "name")
    evidence = line["evidence"]
    if not isinstance(evidence, list):
        raise TypeError("evidence is not a list")
    refs = tuple(EvidenceRef(_text(e["chunk_id"], "chunk_id"), _text(e["span"], "span"))
                 for e in evidence)
    metric = _metric(key, properties, refs, subjects) if label == METRIC_LABEL else None
    return ResolveNode(key=key, label=label, name=name, evidence=refs, metric=metric)


def _metric(key: str, properties: Mapping[str, object], evidence: Sequence[EvidenceRef],
            subjects: Mapping[str, str]) -> MetricFacts:
    if key not in subjects:
        raise ValueError(f"{key} has no MEASURES edge")
    filings = {e.accession_no for e in evidence}
    if len(filings) != 1:
        raise ValueError(f"{key} has evidence from {len(filings)} filings")
    value = properties["value"]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("value is not a number")
    return MetricFacts(accession_no=filings.pop(), subject_key=subjects[key],
                       period=_text(properties["period"], "period"), value=float(value),
                       unit=_text(properties["unit"], "unit"))
