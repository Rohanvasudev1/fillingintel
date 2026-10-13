"""The persistence labeller: gpt-6-luna's verdict on one drawn risk-factor pair (Step 10,
ticket 06).

The request, reply handling and label record are Step 9's (resolve.labeller):
the judge's OpenAI path, a strict LabelReply, quotes checked like Step 8
spans against the chunks shown for their own side, and a PairLabel whose
key_a is the earlier risk factor and key_b the later one. Only the prompt
(`prompts/persist_label/v1.md`, pinned by SHA-256) and what it shows differ:
each factor's title, its filing's form, accession and period, its evidence
spans and the text of its first chunks. It never carries the linker's output:
no candidate rank, method, link or adjudicator answer reaches the labeller.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from eval.judging.openai_judge import OpenAIJudgeRequest
from extract.spans import unescape
from ingest.provenance import REPO_ROOT
from persist.filings import Filing
from persist.sample import SET_NAME
from resolve.labeller import (
    MAX_SPANS_PER_NODE,
    ChunkText,
    LabelOutcome,
    LabelPrompt,
    label_request,
    load_label_prompt,
    send_label_request,
    shown_chunks,
)
from resolve.nodes import ResolveNode
from resolve.sample import DrawnPair
from retrieve.answer_model import AnswerModel

FAMILY = "persist_label"
PROMPTS_DIR = REPO_ROOT / "prompts" / FAMILY
CURRENT_VERSION = "v1"
PUBLISHED = MappingProxyType({
    "v1": "4a01855101bbc0c3511fb6a48b912858f32ad08297bcc427436ed57782734f82",
})
FIELDS = ("earlier", "later")


def load_prompt(version: str = CURRENT_VERSION, prompts_dir: Path = PROMPTS_DIR) -> LabelPrompt:
    """The published prompt *version*; LabelPromptError if its file has changed."""
    return load_label_prompt(version, prompts_dir, published=PUBLISHED, family=FAMILY,
                             fields=FIELDS)


def _factor_text(factor: ResolveNode, filing: Filing, chunk_text: ChunkText) -> str:
    lines = [f"Title: {unescape(factor.name)}",
             f"Filing: {filing.form_type} {filing.accession_no}, period ending "
             f"{filing.report_date.isoformat()}",
             "Evidence spans:"]
    lines += [f'- "{e.span}" (chunk {e.chunk_id})' for e in factor.evidence[:MAX_SPANS_PER_NODE]]
    for chunk_id in shown_chunks(factor):
        lines += ["", f"Chunk {chunk_id}:", "<chunk>", chunk_text(chunk_id), "</chunk>"]
    return "\n".join(lines)


def _filing(factor: ResolveNode, filings: Mapping[str, Filing]) -> Filing:
    accession = factor.evidence[0].accession_no
    if accession not in filings:
        raise ValueError(f"{factor.key} comes from {accession}, which is not in the filing list")
    return filings[accession]


def build_request(prompt: LabelPrompt, earlier: ResolveNode, later: ResolveNode,
                  filings: Mapping[str, Filing], chunk_text: ChunkText) -> OpenAIJudgeRequest:
    """The request for (*earlier*, *later*); it holds the factors' own evidence only."""
    return label_request(
        prompt, earlier=_factor_text(earlier, _filing(earlier, filings), chunk_text),
        later=_factor_text(later, _filing(later, filings), chunk_text))


def label_pair(pair: DrawnPair, earlier: ResolveNode, later: ResolveNode,
               filings: Mapping[str, Filing], chunk_text: ChunkText, backend: AnswerModel,
               prompt: LabelPrompt) -> LabelOutcome:
    """Ask the labeller about *pair*; AnswerModelError (API, cache) stops the caller."""
    if (earlier.key, later.key) != pair.keys:
        raise ValueError(f"factors {earlier.key} / {later.key} are not the pair "
                         f"{pair.key_a} / {pair.key_b}")
    request = build_request(prompt, earlier, later, filings, chunk_text)
    return send_label_request(pair, SET_NAME, request, (shown_chunks(earlier),
                                                        shown_chunks(later)),
                              chunk_text, backend, prompt.prompt_version)
