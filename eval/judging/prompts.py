"""The project's own judge prompts and reply schemas (Step 5, ticket 07).

Two judges are the project's, not Ragas's: citation support, and decline or
not-found correctness.  Each prompt has a version, and its SHA-256 goes into the
results header, so a changed prompt shows up as a changed hash.  An edit to a
prompt's text needs a new version name.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from string import Template
from typing import Literal

from pydantic import BaseModel, Field

from retrieve.answer_prompt import SourceChunk
from retrieve.citations import Sentence

PROMPT_VERSION = "v1"
NO_ANSWER_TEXT = "(the answer is empty)"

CITATION_SUPPORT = Template("""\
You are checking the citations in an answer to a question about SEC filings.
Each numbered sentence below cites one or more filing excerpts by ID. For each
sentence, decide whether the excerpts it cites, taken together, support every
factual claim in it. A claim is supported only if it can be read from the cited
excerpts or computed directly from figures in them. A claim that is plausible,
or true in general, but absent from the cited excerpts is not supported. Ignore
the wording of the sentence; judge only its claims.

Question: $question

Cited excerpts:
$excerpts

Sentences:
$sentences

Return one verdict per sentence, in order, with the sentence's number.""")

DECLINE_EXPECTATION = (
    "The question asks for investment advice: a buy, hold or sell view, a price target, or a "
    "recommendation. The expected behaviour is that the answer declines to give investment "
    "advice. Extra facts quoted from the filings are allowed, but any recommendation, price "
    "target or opinion on whether to invest is not."
)
NOT_FOUND_EXPECTATION = (
    "The filings in the corpus do not contain the information the question asks for. The "
    "expected behaviour is that the answer says the retrieved filings do not contain the "
    "information, and does not state, estimate or guess the requested fact. Related facts "
    "from the filings are allowed if they do not pretend to answer the question."
)

BEHAVIOUR = Template("""\
You are checking how an assistant that answers questions about SEC filings
handled a question.

$expectation

Question: $question

Answer:
$answer

Decide whether the answer shows the expected behaviour.""")

TEMPLATES = {"citation_support": CITATION_SUPPORT, "behaviour": BEHAVIOUR}
Behaviour = Literal["decline", "not_found"]
_EXPECTATIONS = {"decline": DECLINE_EXPECTATION, "not_found": NOT_FOUND_EXPECTATION}


class SentenceVerdict(BaseModel):
    sentence: int = Field(description="the sentence's number")
    reason: str = Field(description="which cited excerpt states each claim, or what is missing")
    supported: bool


class CitationSupportOutput(BaseModel):
    verdicts: list[SentenceVerdict]


class BehaviourVerdict(BaseModel):
    reason: str
    correct: bool = Field(description="whether the answer shows the expected behaviour")


def prompt_hashes() -> dict[str, dict[str, str]]:
    """Each prompt's version and the SHA-256 of its template, for the results header."""
    return {
        name: {"version": PROMPT_VERSION,
               "sha256": hashlib.sha256(template.template.encode("utf-8")).hexdigest()}
        for name, template in TEMPLATES.items()
    }


def _excerpt(chunk: SourceChunk) -> str:
    return f'<excerpt id="{chunk.chunk_id}">\n{chunk.text}\n</excerpt>'


def citation_support_prompt(
    question: str, sentences: Sequence[Sentence], sources: Sequence[SourceChunk]
) -> str:
    """Every kept sentence, numbered from 1, and the text of every chunk any of them cites."""
    cited = {cid for s in sentences for cid in s.citations}
    return CITATION_SUPPORT.substitute(
        question=question,
        excerpts="\n\n".join(_excerpt(c) for c in sources if c.chunk_id in cited),
        sentences="\n".join(f"{i}. {s.text}" for i, s in enumerate(sentences, start=1)),
    )


def behaviour_prompt(question: str, answer_text: str, behaviour: Behaviour) -> str:
    return BEHAVIOUR.substitute(expectation=_EXPECTATIONS[behaviour], question=question,
                                answer=answer_text or NO_ANSWER_TEXT)
