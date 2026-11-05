"""Sample questions: which ``dev`` questions replay per picker option (Step 14 ticket 01).

The fake opener answers only questions it was told are cached and raises
``ArmError`` for any other, as a real arm does under dummy keys.
"""
import json
from contextlib import contextmanager

import pytest

from eval.schema import load_records
from ingest.provenance import REPO_ROOT
from retrieve.arm import ArmError
from ui.samples import Sample, draw_chips, read_samples, select_samples, write_samples

RECORDS = load_records(REPO_ROOT / "eval" / "agent_drafted_set.jsonl")
DEV = [r for r in RECORDS if r.split == "dev"]
TEST = [r for r in RECORDS if r.split == "test"]
CLASSES = ("lookup", "local", "multi_hop", "global", "decline", "unanswerable")


class FakeArm:
    def __init__(self, cached, asked):
        self._cached = cached
        self._asked = asked

    def run(self, question):
        self._asked.append(question)
        if question not in self._cached:
            raise ArmError("answer model call failed: AuthenticationError")
        return object()


def _opener(cached_by_option, asked=None, opened=None):
    asked = [] if asked is None else asked

    @contextmanager
    def open_option(label):
        if opened is not None:
            opened.append(label)
        yield FakeArm(cached_by_option.get(label, set()), asked)

    return open_option


def test_only_questions_that_replay_are_listed_with_why_the_others_did_not():
    cached = {"vector": {r.question for r in DEV[:5]}, "graph_local": {DEV[2].question}}
    samples = select_samples(_opener(cached), DEV[:6], ("vector", "graph_local"))
    assert [s.id for s in samples.options["vector"]] == [r.id for r in DEV[:5]]
    assert [s.id for s in samples.options["graph_local"]] == [DEV[2].id]
    assert samples.options["vector"][0] == Sample(DEV[0].id, DEV[0].class_, DEV[0].question)
    [skipped] = samples.skipped["vector"]
    assert skipped.id == DEV[5].id
    assert "AuthenticationError" in skipped.reason
    assert len(samples.skipped["graph_local"]) == 5


def test_each_option_is_opened_once():
    opened = []
    select_samples(_opener({}, opened=opened), DEV[:4], ("vector", "graph_global"))
    assert opened == ["vector", "graph_global"]


def test_no_test_record_is_ever_asked():
    asked = []
    cached = {"vector": {r.question for r in RECORDS}}
    samples = select_samples(_opener(cached, asked), [*TEST[:3], *DEV[:3]], ("vector",))
    assert set(asked) == {r.question for r in DEV[:3]}
    assert {s.id for s in samples.options["vector"]} == {r.id for r in DEV[:3]}


def test_an_error_other_than_an_arm_error_stops_the_build():
    @contextmanager
    def broken(label):
        class Broken:
            def run(self, question):
                raise RuntimeError("a bug")
        yield Broken()

    with pytest.raises(RuntimeError, match="a bug"):
        select_samples(broken, DEV[:1], ("vector",))


def test_chips_are_one_per_class_drawn_with_a_fixed_seed():
    pool = [Sample(r.id, r.class_, r.question) for r in DEV]
    chips = draw_chips(pool, seed=7)
    assert [c.class_ for c in chips] == list(CLASSES)
    assert draw_chips(pool, seed=7) == chips
    assert draw_chips(list(reversed(pool)), seed=7) == chips  # input order does not matter
    assert draw_chips(pool, seed=8) != chips


def test_a_class_with_no_replaying_question_gets_no_chip():
    pool = [Sample(r.id, r.class_, r.question) for r in DEV if r.class_ != "global"]
    assert "global" not in [c.class_ for c in draw_chips(pool, seed=7)]


def test_the_samples_file_round_trips_and_is_stable(tmp_path):
    cached = {"vector": {r.question for r in DEV[:8]}}
    samples = select_samples(_opener(cached), DEV[:10], ("vector",), seed=3)
    path = tmp_path / "samples.json"
    write_samples(samples, path)
    first = path.read_bytes()
    assert read_samples(path) == samples
    write_samples(read_samples(path), path)
    assert path.read_bytes() == first
    document = json.loads(first)
    assert document["seed"] == 3
    assert [c["id"] for c in document["chips"]] == [c.id for c in samples.chips]


def test_the_committed_samples_file_holds_only_dev_questions_and_six_chips():
    from ui.options import OPTIONS
    from ui.samples import SAMPLES_FILE

    samples = read_samples(SAMPLES_FILE)
    by_id = {r.id: r for r in RECORDS}
    assert set(samples.options) == {o.label for o in OPTIONS}
    for found in samples.options.values():
        for sample in found:
            record = by_id[sample.id]
            assert record.split == "dev"
            assert (sample.class_, sample.question) == (record.class_, record.question)
    assert [c.class_ for c in samples.chips] == list(CLASSES)
    assert set(samples.chips) <= set(samples.options["vector"])
    assert samples.chips == draw_chips(samples.options["vector"], seed=samples.seed)
