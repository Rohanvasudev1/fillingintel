"""Candidates files and a scripted reviewer for `extract.review` tests (Step 8).

Edges are built around real chunks of the NVDA FY2026 10-K fixture: each
evidence span is a slice of its chunk's own text, so highlighting is tested on
real filing text. The edges are review input, not filings.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from tests.graph_test_data import NVDA_10K

PROMPT = "extract/v1@6a0f055a"
NVIDIA = {"label": "Company", "key": {"cik": "1045810"}}
# The edge-type mix of the first real NVDA run, scaled down to keep tests fast.
REAL_MIX = {"REPORTS": 35, "MEASURES": 35, "DISCLOSES": 14, "OFFERS": 8, "SUBJECT_TO": 5,
            "HOLDS_ROLE_AT": 5, "COMPETES_WITH": 4, "PARTY_TO": 3, "AFFECTS": 2,
            "SUPPLIES": 2, "HAS_SEGMENT": 2, "INVOLVED_IN": 2, "OWNS": 1, "CONCERNS": 1}


def text_chunks(nvda_chunks) -> dict[str, str]:
    """chunk_id -> text for every chunk of the NVDA fixture."""
    return {chunk_id: chunk.text for chunk_id, chunk in nvda_chunks.items()}


_WORDS = re.compile(r"[A-Za-z][A-Za-z ,]{40,80}[a-z]")


def _long_chunks(chunks: Mapping[str, str]) -> list[tuple[str, str]]:
    return [(cid, text) for cid, text in sorted(chunks.items()) if len(text) > 400][:25]


def span_of(text: str) -> str:
    """A plain run of words from *text*, so no markdown escape sits at its edge."""
    found = _WORDS.search(text, 100) or _WORDS.search(text)
    return found[0] if found else text[100:180].strip()


def edge_lines(chunks: Mapping[str, str], counts: Mapping[str, int],
               confidences: Iterable[str] = ("stated", "implied", "uncertain")) -> list[dict]:
    """*counts* edges per type, Company -> Organization, each read from one real chunk."""
    sources = _long_chunks(chunks)
    levels = list(confidences)
    lines = []
    i = 0
    for edge_type, n in counts.items():
        for j in range(n):
            chunk_id, text = sources[i % len(sources)]
            end_key = f"Organization:{edge_type.lower()} {j}"
            end_item = f"Organization(key='{end_key}')"
            lines.append({
                "kind": "edge", "item": f"Company(cik='1045810')-[{edge_type}]->{end_item}",
                "type": edge_type, "start": NVIDIA,
                "end": {"label": "Organization", "key": {"key": end_key}},
                "properties": {"chunk_ids": [chunk_id], "evidence_spans": [span_of(text)],
                               "confidences": [levels[i % len(levels)]],
                               "extract_prompts": [PROMPT]},
            })
            i += 1
    return lines


def node_line(key: str, name: str, chunk_id: str, span: str) -> dict:
    return {"kind": "node", "item": f"Organization(key='{key}')", "label": "Organization",
            "properties": {"key": key, "name": name},
            "evidence": [{"chunk_id": chunk_id, "span": span, "confidence": "stated",
                          "extract_prompt": PROMPT}]}


def write_candidates(directory: Path, lines: Iterable[Mapping[str, object]],
                     run: int = 1) -> Path:
    path = directory / f"{NVDA_10K}-run{run}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line, sort_keys=True) + "\n" for line in lines),
                    encoding="utf-8")
    return path


def round_lines(directory: Path, number: int) -> list[dict]:
    path = directory / f"{NVDA_10K}-round{number}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def judgments(lines: list[dict]) -> list[dict]:
    return [line for line in lines if line["kind"] == "judgment"]


class Reviewer:
    """Answers prompts from a script; each answer takes *pace* seconds on a fake clock.

    The script running out reads as end of input (Ctrl-D), which quits.
    """

    def __init__(self, answers: Iterable[str], pace: float = 20.0):
        self._answers = iter(answers)
        self.pace = pace
        self.now = 0.0
        self.prompts: list[str] = []
        self.shown: list[str] = []

    def clock(self) -> float:
        return self.now

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.now += self.pace
        try:
            return next(self._answers)
        except StopIteration:
            raise EOFError from None

    def out(self, *parts: object) -> None:
        self.shown.append(" ".join(str(p) for p in parts))

    @property
    def text(self) -> str:
        return "\n".join(self.shown)
