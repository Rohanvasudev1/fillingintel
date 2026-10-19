"""The judge's per-sentence citation-support verdicts, read from its cached replies
(Step 13 ticket 04).

Run files keep each run's citation-support score, not the verdict behind each
sentence.  The verdicts are recovered offline by rebuilding the judge's request
for each run (the question, the kept sentences and the text of the chunks they
cite, as ``eval.judging.prompts`` renders them) and reading the stored reply
from the response cache.  A run whose request is not cached, or whose reply
cannot be scored, gives None and logs why; nothing is ever sent.  A cache file
that cannot be read raises ``CacheError``.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from eval.judging import prompts
from eval.judging.openai_judge import JUDGE_RUNS, JudgeReplyError, OpenAIJudge
from retrieve.answer_model import AnswerModelError, ApiResponse, ModelRequest
from retrieve.answer_prompt import SourceChunk
from retrieve.citations import Sentence
from retrieve.response_cache import CachedAnswerModel

Verdicts = tuple[bool, ...] | None  # one per kept sentence, or None for a missing run
logger = logging.getLogger(__name__)


class _NoCalls:
    """Stands in for the API: a request missing from the cache is never sent."""

    def complete(self, request: ModelRequest) -> ApiResponse:
        raise AnswerModelError("not in the response cache")


def judge_sources(retrieved: Sequence[str], texts: Mapping[str, str]) -> tuple[SourceChunk, ...]:
    """The retrieved chunks as the judges read them: only the ID and text are used."""
    return tuple(SourceChunk(c, "", "", "", "", texts[c]) for c in retrieved)


def cached_citation_verdicts(question: str, kept: Sequence[Sentence],
                             sources: Sequence[SourceChunk], response_cache: Path,
                             runs: int = JUDGE_RUNS) -> tuple[Verdicts, ...]:
    """Each judge run's verdicts for *kept*, in run order."""
    cache = CachedAnswerModel(_NoCalls(), response_cache)
    prompt = prompts.citation_support_prompt(question, kept, sources)
    found: list[Verdicts] = []
    for run in range(1, runs + 1):
        judge = OpenAIJudge(cache, run)
        try:
            reply = judge.generate(prompt, prompts.CitationSupportOutput)
        except (AnswerModelError, JudgeReplyError) as exc:  # a miss, or an unusable reply
            logger.warning("citation verdicts, run %d: %s", run, exc)
            found.append(None)
            continue
        numbers = [v.sentence for v in reply.verdicts]
        if numbers != list(range(1, len(kept) + 1)):
            logger.warning("citation verdicts, run %d: numbered %s for %d sentences", run,
                           numbers, len(kept))
            found.append(None)
            continue
        found.append(tuple(v.supported for v in reply.verdicts))
    return tuple(found)


def majority(runs: Sequence[Verdicts], index: int) -> bool | None:
    """Sentence *index*'s verdict in most runs that gave one; a tie or no run gives None."""
    votes = [v[index] for v in runs if v is not None]
    yes = sum(votes)
    if not votes or yes * 2 == len(votes):
        return None
    return yes * 2 > len(votes)
