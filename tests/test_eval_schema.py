"""Tests for eval-set records and JSONL files (Step 4)."""
import json

import pytest
from pydantic import ValidationError

from eval.corpus_index import ChunkIndex
from eval.schema import dump_records, load_records, split_for
from tests.eval_helpers import make_record


@pytest.fixture(scope="module")
def index(fixture_records):
    return ChunkIndex.from_records(fixture_records)


def test_valid_record_round_trips_through_jsonl(tmp_path, index):
    record = make_record(index)
    path = tmp_path / "set.jsonl"
    dump_records([record], path)
    assert load_records(path) == [record]
    assert json.loads(path.read_text().splitlines()[0])["class"] == "lookup"


def test_answerable_class_needs_gold_chunks(index):
    with pytest.raises(ValidationError, match="gold_chunk_ids"):
        make_record(index, gold_chunk_ids=[], gold_text_sha256={})


@pytest.mark.parametrize("cls", ["decline", "unanswerable"])
def test_decline_and_unanswerable_have_no_gold_chunks(index, cls):
    record = make_record(index, gold_chunk_ids=[], gold_text_sha256={}, **{"class": cls})
    assert record.gold_chunk_ids == []
    with pytest.raises(ValidationError, match="no gold"):
        make_record(index, **{"class": cls})


def test_hashes_must_cover_exactly_the_gold_chunks(index):
    gold = index.chunk_ids()[0]
    with pytest.raises(ValidationError, match="gold_text_sha256"):
        make_record(index, gold_text_sha256={gold: "0" * 64, "x:0001": "0" * 64})


def test_hard_negative_cannot_also_be_gold(index):
    gold = index.chunk_ids()[0]
    with pytest.raises(ValidationError, match="hard negative"):
        make_record(index, hard_negatives=[{"chunk_id": gold, "relation": "peer_company"}])


def test_at_most_two_hard_negatives(index):
    negs = [{"chunk_id": c, "relation": "peer_company"} for c in index.chunk_ids()[1:4]]
    with pytest.raises(ValidationError):
        make_record(index, hard_negatives=negs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "question-1"),
        ("class", "trivia"),
        ("provenance", "llm"),
        ("split", "train"),
        ("tickers", ["TSLA"]),
        ("difficulty", "extreme"),
        ("question", "   "),
    ],
)
def test_bad_field_values_are_rejected(index, field, value):
    with pytest.raises(ValidationError):
        make_record(index, **{field: value})


def test_duplicate_ids_in_a_file_are_rejected(tmp_path, index):
    record = make_record(index)
    path = tmp_path / "set.jsonl"
    line = record.model_dump_json(by_alias=True)
    path.write_text(f"{line}\n{line}\n")
    with pytest.raises(ValueError, match="duplicate id q0001"):
        load_records(path)


def test_bad_line_reports_its_line_number(tmp_path, index):
    path = tmp_path / "set.jsonl"
    path.write_text(make_record(index).model_dump_json(by_alias=True) + "\n{not json}\n")
    with pytest.raises(ValueError, match="line 2"):
        load_records(path)


def test_split_is_deterministic_and_roughly_thirty_percent():
    questions = [f"Question number {i} about revenue?" for i in range(1000)]
    splits = [split_for(q) for q in questions]
    assert splits == [split_for(q) for q in questions]
    assert 0.25 < splits.count("test") / len(splits) < 0.35


@pytest.mark.parametrize("cls", ["decline", "unanswerable"])
def test_edge_classes_have_no_hard_negatives(index, cls):
    neg = [{"chunk_id": index.chunk_ids()[1], "relation": "peer_company"}]
    with pytest.raises(ValidationError, match="hard negatives"):
        make_record(
            index, gold_chunk_ids=[], gold_text_sha256={}, hard_negatives=neg, **{"class": cls}
        )


def test_hard_negatives_must_be_distinct(index):
    neg = {"chunk_id": index.chunk_ids()[1], "relation": "peer_company"}
    with pytest.raises(ValidationError, match="repeat"):
        make_record(index, hard_negatives=[neg, neg])


def test_human_verified_needs_derived_from_and_others_forbid_it(index):
    with pytest.raises(ValidationError, match="derived_from"):
        make_record(index, provenance="human_verified")
    with pytest.raises(ValidationError, match="derived_from"):
        make_record(index, provenance="human_written", derived_from="a" * 16)


def test_draft_key_is_stable_and_ignores_case_spacing_and_id(index):
    from eval.schema import draft_key

    a = make_record(index, id="q0001", question="What was revenue?")
    b = make_record(index, id="q0009", question="  what WAS   revenue? ")
    assert draft_key(a) == draft_key(b)
    c = make_record(index, question="What was revenue?", gold_chunk_ids=[index.chunk_ids()[1]])
    assert draft_key(c) != draft_key(a)
