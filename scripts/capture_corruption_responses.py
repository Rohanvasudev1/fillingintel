"""Copy the corruption test's judge replies for one answer into the test fixtures
(Step 13 ticket 04).  Sends nothing: every reply must already be in the response
cache from ``python -m eval.corrupt``, and a missing one stops the script.

    uv run python scripts/capture_corruption_responses.py

Copies, for ``graph_traversal:q0070``, the replies to its corrupted copies and to its
original citation-support requests into tests/fixtures/judge/corruption, where
tests/test_corruption_recorded.py replays them.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from eval.calibration.answers import load_answers  # noqa: E402
from eval.calibration.corruption import METRICS, judge_planned, plan  # noqa: E402
from eval.calibration.judge_verdicts import judge_sources  # noqa: E402
from eval.corpus_index import ChunkIndex  # noqa: E402
from eval.judging import prompts  # noqa: E402
from eval.judging.openai_judge import JUDGE_RUNS, OpenAIJudge  # noqa: E402
from eval.judging.scoring import RagasJudges  # noqa: E402
from ingest.parsed_files import DEFAULT_PARSED_DIR  # noqa: E402
from retrieve.answer_model import AnswerModelError, ApiResponse, ModelRequest  # noqa: E402
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel  # noqa: E402

KEY = "graph_traversal:q0070"
FIXTURES = REPO / "tests" / "fixtures" / "judge" / "corruption"


class _NoCalls:
    def complete(self, request: ModelRequest) -> ApiResponse:
        raise AnswerModelError(f"not in the response cache: {request.cache_key()}")


class _Recorder:
    """The real response cache, never calling out, noting each request it answers."""

    def __init__(self) -> None:
        self._cache = CachedAnswerModel(_NoCalls(), DEFAULT_CACHE_DIR)
        self.files: set[str] = set()

    def complete(self, request: ModelRequest) -> ApiResponse:
        self.files.add(f"{request.model}/{request.effort}/{request.cache_key()}.json")
        return self._cache.complete(request)


def main() -> int:
    answer = next(a for a in load_answers() if a.key == KEY)
    index = ChunkIndex.from_parsed_dir(DEFAULT_PARSED_DIR)
    texts = {c: index.text(c) for c in answer.retrieved}
    planned, _, _ = plan([answer], texts)
    recorder = _Recorder()
    # Relevancy is not judged, so the embedder is never used.
    judges = RagasJudges(recorder, embedder=None, metrics=METRICS)  # type: ignore[arg-type]
    judge_planned(judges, planned, DEFAULT_CACHE_DIR)
    original = prompts.citation_support_prompt(answer.question, answer.kept,
                                               judge_sources(answer.retrieved, texts))
    for run in range(1, JUDGE_RUNS + 1):
        OpenAIJudge(recorder, run).generate(original, prompts.CitationSupportOutput)
    for name in sorted(recorder.files):
        target = FIXTURES / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_CACHE_DIR / name, target)
    print(f"copied {len(recorder.files)} replies to {FIXTURES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
