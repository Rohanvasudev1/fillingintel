"""The corruption test on recorded judge replies (Step 13 ticket 04).

``graph_traversal:q0070`` is a drawn corruption-test answer whose retrieved chunks
all sit in the fixture filings, so its corrupted copies can be rebuilt offline.
Its gpt-6-luna replies, copied from the real run's response cache into
tests/fixtures/judge/corruption, are replayed with no network call; the copies'
scores and catches must equal what the committed run file holds.
"""
import json

import pytest

from eval.calibration.answers import RUNS_DIR, load_answers
from eval.calibration.catch import catch_rates
from eval.calibration.corruption import METRICS, judge_planned, plan
from eval.calibration.judge_verdicts import cached_citation_verdicts, judge_sources
from eval.corpus_index import ChunkIndex
from eval.judging.scoring import RagasJudges
from ingest.provenance import REPO_ROOT
from retrieve.answer_model import AnswerModelError
from retrieve.response_cache import CachedAnswerModel
from tests.judge_fakes import FixedEmbedder

KEY = "graph_traversal:q0070"
RUN = REPO_ROOT / "benchmarks" / "calibration" / "corruption-run1.json"
RESPONSES = REPO_ROOT / "tests" / "fixtures" / "judge" / "corruption"


class _NoCalls:
    def complete(self, request):
        raise AnswerModelError("not recorded")


@pytest.fixture(scope="module")
def recorded(fixture_records):
    answer = next(a for a in load_answers(RUNS_DIR) if a.key == KEY)
    index = ChunkIndex.from_records(fixture_records)
    texts = {c: index.text(c) for c in answer.retrieved}
    planned, skipped, _ = plan([answer], texts)
    judges = RagasJudges(CachedAnswerModel(_NoCalls(), RESPONSES), FixedEmbedder({}),
                         metrics=METRICS)
    results, _ = judge_planned(judges, planned, RESPONSES)
    run = json.loads(RUN.read_text(encoding="utf-8"))
    return answer, texts, results, skipped, [c for c in run["copies"] if c["key"] == KEY]


def test_recorded_replies_give_the_committed_scores(recorded):
    _, _, results, _, committed = recorded
    assert [(r.kind, list(r.corrupted_faithfulness)) for r in results] == \
        [(c["kind"], c["corrupted_faithfulness"]) for c in committed]
    assert [[None if v is None else list(v) for v in r.corrupted_verdicts] for r in results] \
        == [c["corrupted_verdicts"] for c in committed]


def test_catch_rates_come_from_the_recorded_replies(recorded):
    _, _, results, skipped, _ = recorded
    rates = catch_rates(results, skipped)
    for row in rates.values():
        faith = row["faithfulness"]
        assert faith["scored"] + faith["unscored"] == row["applied"]
    assert rates["wrong_citation"]["faithfulness"]["caught"] == 0, \
        "faithfulness ignores citations"


def test_the_original_verdicts_replay_from_the_cache(recorded):
    answer, texts, _, _, committed = recorded
    verdicts = cached_citation_verdicts(answer.question, answer.kept,
                                        judge_sources(answer.retrieved, texts), RESPONSES)
    assert all(v is not None and len(v) == len(answer.kept) for v in verdicts)
    number = next(c for c in committed if c["kind"] == "changed_number")
    assert [list(v) for v in verdicts] == number["original_verdicts"]
