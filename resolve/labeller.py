"""The labeller: gpt-6-luna's verdict on one drawn pair (Step 9, ticket 04).

The request goes through the judge's OpenAI path unchanged: an
`OpenAIJudgeRequest` (Responses API, strict json_schema, store=false, no
sampling settings) sent through whatever backend the caller passes, normally
the response cache around `OpenAIResponsesModel`. Only the prompt differs.

The prompt (`prompts/label/v2.md`, pinned by SHA-256) carries each node's
name, the shared label, its evidence spans and the text of its first chunks
(and a MetricValue's subject, period, value and unit). It never carries the
resolver's output: no rule, score, stratum or decision reaches the labeller,
so its labels are independent of what they score.

Each reply's quotes are checked like Step 8 spans (extract.spans): a quote
must be found in one of its own node's shown chunks. A label with a quote that
fails is kept with `quotes_verified` false and counted in the report.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, ValidationError

from eval.judging.openai_backend import OpenAIUsage, parse_openai_reply
from eval.judging.openai_judge import OpenAIJudgeRequest, output_schema
from extract.spans import find_span
from ingest.provenance import REPO_ROOT
from resolve.labels import PairLabel
from resolve.nodes import ResolveNode
from resolve.sample import DrawnPair
from retrieve.answer_model import AnswerModel, AnswerModelError, Effort
from retrieve.pricing import openai_cost

LABEL_PROVIDER = "openai"
LABEL_MODEL = "gpt-6-luna"
LABEL_EFFORT: Effort = "high"
LABEL_NOTE = f"agent-labelled ({LABEL_MODEL})"  # on every figure built on these labels
LABEL_MAX_OUTPUT_TOKENS = 16_000  # reasoning plus JSON at effort high
MAX_CHUNKS_PER_NODE = 3  # chunk texts shown per node, in evidence order
MAX_SPANS_PER_NODE = 10
PROMPTS_DIR = REPO_ROOT / "prompts" / "label"
CURRENT_VERSION = "v2"
PUBLISHED = MappingProxyType({
    "v1": "9d65910323ebcd83b0340368a3779d152e54228f788ecb52fcaff91234411f04",
    "v2": "97a4a047620d308e2570967cbc520653f27fc059316830e9ef5be946778aeb6d",  # invented examples
})
_HASH_CHARS = 8

ChunkText = Callable[[str], str]  # chunk ID to the text resolve() returns


class LabelPromptError(RuntimeError):
    """A label prompt version is unknown, changed after publication, or malformed."""


class LabelReply(BaseModel):
    """The labeller's reply; the reason and quotes come before the verdict."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    quote_a: str
    chunk_id_a: str
    quote_b: str
    chunk_id_b: str
    same: bool


@dataclass(frozen=True, slots=True)
class LabelPrompt:
    version: str
    file_sha256: str
    sha256: str  # of the template and the output schema
    template: str

    @property
    def prompt_version(self) -> str:
        return f"label/{self.version}@{self.sha256[:_HASH_CHARS]}"


def load_label_prompt(version: str = CURRENT_VERSION,
                      prompts_dir: Path = PROMPTS_DIR) -> LabelPrompt:
    """The published prompt *version*; LabelPromptError if its file has changed."""
    if version not in PUBLISHED:
        raise LabelPromptError(f"label prompt {version} is not published")
    try:
        raw = (prompts_dir / f"{version}.md").read_bytes()
    except OSError as exc:
        raise LabelPromptError(f"cannot read label prompt {version}: "
                               f"{type(exc).__name__}") from exc
    file_sha = hashlib.sha256(raw).hexdigest()
    if file_sha != PUBLISHED[version]:
        raise LabelPromptError(f"label prompt {version} has changed since it was published; "
                               "write the change as a new version instead")
    template = raw.decode("utf-8")
    try:
        Template(template).substitute(label="", node_a="", node_b="")
    except (KeyError, ValueError) as exc:
        raise LabelPromptError(f"label prompt {version} has a bad placeholder") from exc
    sent = json.dumps({"template": template, "schema": output_schema(LabelReply)},
                      sort_keys=True, ensure_ascii=False)
    return LabelPrompt(version, file_sha, hashlib.sha256(sent.encode()).hexdigest(), template)


def shown_chunks(node: ResolveNode) -> tuple[str, ...]:
    return node.chunk_ids[:MAX_CHUNKS_PER_NODE]


def _node_text(node: ResolveNode, chunk_text: ChunkText) -> str:
    lines = [f"Name: {node.name}"]
    if node.metric is not None:
        m = node.metric
        lines.append(f"Subject: {m.subject_key}; period: {m.period}; value: {m.value:g}; "
                     f"unit: {m.unit}")
    lines.append("Evidence spans:")
    lines += [f'- "{e.span}" (chunk {e.chunk_id})' for e in node.evidence[:MAX_SPANS_PER_NODE]]
    for chunk_id in shown_chunks(node):
        lines += ["", f"Chunk {chunk_id}:", "<chunk>", chunk_text(chunk_id), "</chunk>"]
    return "\n".join(lines)


def build_label_request(prompt: LabelPrompt, a: ResolveNode, b: ResolveNode,
                        chunk_text: ChunkText) -> OpenAIJudgeRequest:
    """The request for the pair (*a*, *b*); it holds the nodes' own evidence and nothing else."""
    if a.label != b.label:
        raise ValueError(f"{a.key} and {b.key} have different labels")
    text = Template(prompt.template).substitute(
        label=a.label, node_a=_node_text(a, chunk_text), node_b=_node_text(b, chunk_text))
    return OpenAIJudgeRequest(model=LABEL_MODEL, effort=LABEL_EFFORT,
                              max_output_tokens=LABEL_MAX_OUTPUT_TOKENS, prompt=text,
                              schema=output_schema(LabelReply), schema_name="LabelReply",
                              run=1, repeat=0, provider=LABEL_PROVIDER)


@dataclass(frozen=True, slots=True)
class LabelOutcome:
    """One labeller call: the label, or why there is none, and what the call cost."""

    pair: DrawnPair
    label: PairLabel | None
    failure: str | None
    usage: OpenAIUsage
    cost_usd: float
    from_cache: bool


def label_pair(pair: DrawnPair, set_name: str, a: ResolveNode, b: ResolveNode,
               chunk_text: ChunkText, backend: AnswerModel,
               prompt: LabelPrompt) -> LabelOutcome:
    """Ask the labeller about *pair*; AnswerModelError (API, cache) stops the caller."""
    if (a.key, b.key) != pair.keys:
        raise ValueError(f"nodes {a.key} / {b.key} are not the pair {pair.key_a} / {pair.key_b}")
    response = backend.complete(build_label_request(prompt, a, b, chunk_text))
    reply = parse_openai_reply(response.body)
    spent = {"usage": reply.usage, "cost_usd": openai_cost(LABEL_MODEL, reply.usage),
             "from_cache": response.from_cache}
    failure = _unusable(reply.refusal, reply.status, reply.incomplete_reason)
    if failure is None and reply.status != "completed":
        raise AnswerModelError(f"labeller response {reply.response_id} is {reply.status}")
    if failure is not None:
        return LabelOutcome(pair, None, failure, **spent)
    try:
        parsed = LabelReply.model_validate_json(reply.text)
    except ValidationError as exc:
        return LabelOutcome(pair, None, f"invalid_reply ({exc.error_count()} errors)", **spent)
    verified = (_quote_found(parsed.quote_a, parsed.chunk_id_a, a, chunk_text)
                and _quote_found(parsed.quote_b, parsed.chunk_id_b, b, chunk_text))
    label = PairLabel(
        set_name=set_name, key_a=pair.key_a, key_b=pair.key_b, label=pair.label,
        stratum=pair.stratum, same=parsed.same, quote_a=parsed.quote_a,
        chunk_id_a=parsed.chunk_id_a, quote_b=parsed.quote_b, chunk_id_b=parsed.chunk_id_b,
        quotes_verified=verified, reason=parsed.reason, model=LABEL_MODEL, effort=LABEL_EFFORT,
        prompt_version=prompt.prompt_version)
    return LabelOutcome(pair, label, None, **spent)


def _unusable(refusal: str | None, status: str | None, reason: str | None) -> str | None:
    if refusal is not None:
        return "refused"
    if status == "incomplete":
        return f"incomplete ({reason or 'no reason given'})"
    return None


def _quote_found(quote: str, chunk_id: str, node: ResolveNode, chunk_text: ChunkText) -> bool:
    return chunk_id in shown_chunks(node) and find_span(quote, chunk_text(chunk_id)) is not None

