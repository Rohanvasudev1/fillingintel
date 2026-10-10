"""The merge log and replay() (Step 9, ADR-0006)."""
import json

import pytest

from resolve.groups import canonical_key
from resolve.log import (
    EvidenceQuote,
    LogError,
    MergeEvent,
    Method,
    SplitEvent,
    Stamp,
    append_events,
    merge_event,
    new_merges,
    read_log,
    replay,
    split_event,
)
from resolve.paths import ALIAS_MAP, MERGE_LOG, read_alias_map

STAMP = Stamp(config_hash="c" * 64, commit="abc1234", timestamp="2026-10-10T12:00:00+00:00")
GROQ = {"Organization:groq": 1, "Organization:groq, inc": 1, "Organization:groq, inc.": 1}


def _merge(members, rule="normalized_equal", label="Organization"):
    evidence = tuple(EvidenceQuote(key, key.split(":", 1)[1], "0001045810-26-000021:0010",
                                   "Groq") for key in members)
    return merge_event(label=label, members=members, method=Method.DETERMINISTIC, rule=rule,
                       evidence=evidence, stamp=STAMP)


def test_replay_maps_every_member_to_the_canonical_key():
    assert replay([_merge(GROQ)]) == {key: "Organization:groq" for key in GROQ}


def test_merge_then_split_restores_both_keys():
    merge = _merge({"Regulation:gdpr": 1, "Regulation:general data protection regulation": 1},
                   rule="acronym")
    split = split_event(merge.merge_id, Method.USER, "GDPR here is the U.K. regime", STAMP)
    assert replay([merge, split]) == {}


def test_replay_is_the_same_every_time_and_in_key_order():
    events = [_merge(GROQ), _merge({"Person:mr. teter": 1, "Person:timothy s. teter": 2},
                                   label="Person")]
    first = replay(events)
    assert all(replay(events) == first for _ in range(3))
    assert list(first) == sorted(first)
    assert first["Person:mr. teter"] == "Person:timothy s. teter"


def test_overlapping_merges_join_into_one_group():
    a = _merge({"Organization:groq": 1, "Organization:groq, inc": 1})
    b = _merge({"Organization:groq, inc": 1, "Organization:groq, inc.": 3}, rule="fuzzy")
    assert set(replay([a, b]).values()) == {"Organization:groq, inc."}


def test_a_split_of_an_unknown_merge_is_rejected():
    with pytest.raises(LogError, match="unknown merge"):
        replay([split_event("m-0000000000000000", Method.USER, "no such merge", STAMP)])


def test_a_merge_cannot_be_split_twice():
    merge = _merge(GROQ)
    split = split_event(merge.merge_id, Method.USER, "wrong", STAMP)
    with pytest.raises(LogError, match="already split"):
        replay([merge, split, split])


def test_canonical_key_is_the_member_with_most_chunks_ties_to_the_smallest_key():
    assert canonical_key({"Product:geforce": 2, "Product:geforce gpus": 5}) == \
        "Product:geforce gpus"
    assert canonical_key({"Organization:groq, inc.": 1, "Organization:groq": 1}) == \
        "Organization:groq"


def test_a_filer_company_is_always_canonical():
    assert canonical_key({"Organization:nvidia corporation": 9, "Company:1045810": 0}) == \
        "Company:1045810"


def test_new_merges_skips_merges_already_logged_even_when_split():
    merge = _merge(GROQ)
    split = split_event(merge.merge_id, Method.USER, "wrong", STAMP)
    again = _merge(GROQ)
    assert again.merge_id == merge.merge_id
    assert new_merges([merge, split], [again]) == ()
    other = _merge({"Organization:huawei": 1, "Organization:huawei technologies co. ltd.": 1})
    assert new_merges([merge], [again, other]) == (other,)


def test_events_round_trip_through_the_file(tmp_path):
    path = tmp_path / "merge_log.jsonl"
    merge = _merge(GROQ)
    split = split_event(merge.merge_id, Method.USER, "wrong", STAMP)
    append_events(path, [merge])
    append_events(path, [split])
    assert read_log(path) == (merge, split)
    assert isinstance(read_log(path)[0], MergeEvent)
    assert isinstance(read_log(path)[1], SplitEvent)


def test_append_keeps_earlier_lines_byte_for_byte(tmp_path):
    path = tmp_path / "merge_log.jsonl"
    append_events(path, [_merge(GROQ)])
    before = path.read_bytes()
    append_events(path, [_merge({"Organization:samsung": 1,
                                 "Organization:samsung electronics co., ltd.": 1})])
    assert path.read_bytes().startswith(before)


def test_a_malformed_line_is_a_log_error(tmp_path):
    path = tmp_path / "merge_log.jsonl"
    path.write_text(json.dumps({"event": "merge"}) + "\n", encoding="utf-8")
    with pytest.raises(LogError, match="line 1"):
        read_log(path)


def test_a_merge_needs_two_members():
    with pytest.raises(ValueError, match="two"):
        _merge({"Organization:groq": 1})


def test_the_committed_alias_map_equals_replay_of_the_committed_log():
    assert MERGE_LOG.exists()
    assert read_alias_map(ALIAS_MAP) == replay(read_log(MERGE_LOG))
