"""Run the judges over every scored question, three runs each (Step 5, ticket 07).

Judging starts once the arm has answered every question.  Each (question, run)
pair is one job; jobs run on a small thread pool, because a judge call takes
seconds and the cache makes the result independent of the order.  A failure
that is not about one reply (the API, Voyage or the cache) raises
``JudgeRunError`` and stops the run, so no partial results file is written.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from eval.judging.openai_backend import API_KEY_ENV as OPENAI_KEY_ENV
from eval.judging.openai_backend import BASE_URL_ENV, OpenAIResponsesModel
from eval.judging.scoring import Judge, JudgeInput, RagasJudges, RunScores
from ingest.voyage import API_KEY_ENV as VOYAGE_KEY_ENV
from ingest.voyage import VoyageClient, VoyageError
from retrieve.answer_model import AnswerModelError
from retrieve.query_cache import CachedQueryEmbedder, CacheError
from retrieve.response_cache import CachedAnswerModel

JUDGE_WORKERS = 8  # concurrent judge jobs; the SDK backs off on 429
PROGRESS_EVERY = 25

logger = logging.getLogger(__name__)


class JudgeRunError(RuntimeError):
    """The judges could not start or could not finish; the message is safe to print."""


@dataclass(frozen=True)
class JudgedQuestion:
    """Every judge run for one question, in run order."""

    runs: tuple[RunScores, ...]


@dataclass(frozen=True)
class JudgeSpec:
    """How the harness opens the judges, and the environment variables they need or refuse."""

    required_env: tuple[str, ...]
    open: Callable[[Path], AbstractContextManager[Judge]]  # takes the response cache folder
    forbidden_env: tuple[str, ...] = ()  # must be unset, or the run does not start


def judge_all(judge: Judge, items: Sequence[JudgeInput],
              workers: int = JUDGE_WORKERS) -> list[JudgedQuestion]:
    """*judge*'s runs 1 to ``config.runs`` for each of *items*, in the order of *items*."""
    jobs = [(i, run) for i in range(len(items)) for run in range(1, judge.config.runs + 1)]
    done: dict[tuple[int, int], RunScores] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(judge.judge, items[i], run): (i, run) for i, run in jobs}
        try:
            for count, future in enumerate(as_completed(futures), start=1):
                done[futures[future]] = future.result()
                if count % PROGRESS_EVERY == 0:
                    logger.info("%d of %d judge runs", count, len(jobs))
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    return [
        JudgedQuestion(tuple(done[(i, run)] for run in range(1, judge.config.runs + 1)))
        for i in range(len(items))
    ]


class _GuardedJudges:
    """Turns an API, Voyage or cache failure into ``JudgeRunError``."""

    def __init__(self, inner: RagasJudges):
        self._inner = inner
        self.config = inner.config

    def judge(self, item: JudgeInput, run: int) -> RunScores:
        try:
            return self._inner.judge(item, run)
        except (AnswerModelError, VoyageError, CacheError, OSError) as exc:  # OSError: cache write
            raise JudgeRunError(f"judging failed ({type(exc).__name__}): {exc}") from exc
        except Exception as exc:  # a Ragas or validation bug: stop cleanly, keep the trace
            logger.exception("unexpected judging failure")
            raise JudgeRunError(f"judging failed unexpectedly ({type(exc).__name__}): {exc}") \
                from exc


@contextmanager
def open_judges(response_cache: Path) -> Iterator[Judge]:
    """The OpenAI judge model and Voyage, each behind its disk cache."""
    with ExitStack() as stack:
        try:
            backend = stack.enter_context(OpenAIResponsesModel.from_env())
            voyage = stack.enter_context(VoyageClient.from_env())
        except (AnswerModelError, VoyageError) as exc:
            raise JudgeRunError(f"cannot start the judges: {exc}") from exc
        yield _GuardedJudges(RagasJudges(CachedAnswerModel(backend, response_cache),
                                           CachedQueryEmbedder(voyage)))


JUDGES = JudgeSpec(required_env=(OPENAI_KEY_ENV, VOYAGE_KEY_ENV), open=open_judges,
                   forbidden_env=(BASE_URL_ENV,))
