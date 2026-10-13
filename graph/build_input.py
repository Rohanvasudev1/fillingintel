"""What the graph builder reads (Step 10): structural rows and extract files.

Structural rows come from Postgres (graph/structural.py). Extract files are
the candidates files `extract.run` writes, `{accession_no}-run{N}.jsonl`, one
JSON object per line: a `node`, an `edge` or a `flag`. Flags are review notes
the builder does not use. Any other line, or a node or edge missing a field,
fails with InputError naming the file and line, since these files come from
outside the process.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType

from graph.batch import EVIDENCE_LISTS, Evidence, NodeRef

FILE_NAME = re.compile(r"(\d{10}-\d{2}-\d{6})-run(\d+)\.jsonl")
NODE, EDGE, FLAG = "node", "edge", "flag"


class InputError(ValueError):
    """An input file is missing or does not hold what its writer writes."""


@dataclass(frozen=True, slots=True)
class FilingRow:
    accession_no: str
    cik: str
    company_name: str
    form_type: str
    fiscal_period: str
    filing_date: date


@dataclass(frozen=True, slots=True)
class ChunkRow:
    chunk_id: str
    accession_no: str
    section: str


@dataclass(frozen=True, slots=True)
class StructuralRows:
    filings: tuple[FilingRow, ...]
    chunks: tuple[ChunkRow, ...]


@dataclass(frozen=True, slots=True)
class ExtractNode:
    """One node line: its label, properties (key included) and evidence."""

    label: str
    properties: Mapping[str, object]
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "properties", MappingProxyType(dict(self.properties)))

    @property
    def key(self) -> str:
        return str(self.properties["key"])


@dataclass(frozen=True, slots=True)
class ExtractEdge:
    """One edge line: its endpoints, identity role, stake and evidence entries."""

    type: str
    start: NodeRef
    end: NodeRef
    role: str | None
    stake: float | None
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class ExtractFile:
    accession_no: str
    run: int
    sha256: str
    nodes: tuple[ExtractNode, ...]
    edges: tuple[ExtractEdge, ...]

    @property
    def name(self) -> str:
        return f"{self.accession_no}-run{self.run}.jsonl"


def latest_extract_files(directory: Path) -> tuple[Path, ...]:
    """The highest-numbered extract file of each accession in *directory*, by accession."""
    latest: dict[str, tuple[int, Path]] = {}
    if directory.is_dir():
        for path in directory.iterdir():
            if (m := FILE_NAME.fullmatch(path.name)) and int(m[2]) > latest.get(m[1], (0,))[0]:
                latest[m[1]] = (int(m[2]), path)
    return tuple(path for _, (_, path) in sorted(latest.items()))


def read_extract_file(path: Path) -> ExtractFile:
    """The nodes and edges of one extract file; InputError names any bad line."""
    match = FILE_NAME.fullmatch(path.name)
    if match is None:
        raise InputError(f"{path.name} is not named {{accession_no}}-run{{N}}.jsonl")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read {path.name}: {type(exc).__name__}") from exc
    nodes: list[ExtractNode] = []
    edges: list[ExtractEdge] = []
    for number, text in enumerate(raw.decode("utf-8").splitlines(), start=1):
        try:
            line = _object(json.loads(text), "the line")
            kind = line.get("kind")
            if kind == NODE:
                nodes.append(_node(line))
            elif kind == EDGE:
                edges.append(_edge(line))
            elif kind != FLAG:
                raise ValueError(f"unknown kind {kind!r}")
        except ValueError as exc:  # json.JSONDecodeError is a ValueError
            raise InputError(f"{path.name} line {number}: {exc}") from exc
    return ExtractFile(match[1], int(match[2]), hashlib.sha256(raw).hexdigest(),
                       tuple(nodes), tuple(edges))


# ── parsing one line ──────────────────────────────────────────────────────────

def _object(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} is not an object")
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is not a non-blank string")
    return value


def _texts(value: object, what: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{what} is not a list")
    return [_text(v, what) for v in value]


def _evidence(raw: object) -> Evidence:
    entry = _object(raw, "an evidence entry")
    return Evidence(*(_text(entry.get(name), name)
                      for name in ("chunk_id", "span", "confidence", "extract_prompt")))


def _node(line: Mapping[str, object]) -> ExtractNode:
    properties = _object(line.get("properties"), "properties")
    _text(properties.get("key"), "key")
    raw_evidence = line.get("evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise ValueError("a node needs a non-empty evidence list")
    return ExtractNode(_text(line.get("label"), "label"), properties,
                       tuple(_evidence(e) for e in raw_evidence))


def _ref(raw: object, side: str) -> NodeRef:
    ref = _object(raw, side)
    key = _object(ref.get("key"), f"{side} key")
    return NodeRef.of(_text(ref.get("label"), f"{side} label"),
                      **{k: _text(v, f"{side} key {k}") for k, v in key.items()})


def _edge(line: Mapping[str, object]) -> ExtractEdge:
    properties = _object(line.get("properties"), "properties")
    lists = [_texts(properties.get(name), name) for name in EVIDENCE_LISTS]
    if not lists[0] or len({len(v) for v in lists}) != 1:
        raise ValueError("the evidence lists must be non-empty and of one length")
    role = properties.get("role")
    stake = properties.get("stake")
    if stake is not None and (isinstance(stake, bool) or not isinstance(stake, int | float)):
        raise ValueError("stake is not a number")
    return ExtractEdge(
        _text(line.get("type"), "type"), _ref(line.get("start"), "start"),
        _ref(line.get("end"), "end"),
        None if role is None else _text(role, "role"),
        None if stake is None else float(stake),
        tuple(Evidence(*entry) for entry in zip(*lists, strict=True)),
    )
