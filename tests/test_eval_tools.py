"""Tests for the eval-set tooling: chunk index, validator, coverage, search (Step 4)."""
import pytest

from eval.corpus_index import ChunkIndex
from eval.coverage import TARGETS, coverage, format_coverage
from eval.schema import dump_records
from eval.search import main as search_main
from eval.validate import main as validate_main
from eval.validate import validate
from ingest.chunker import chunk_filing
from ingest.parsed_files import write_parsed
from tests.eval_helpers import make_record


@pytest.fixture(scope="module")
def index(fixture_records):
    return ChunkIndex.from_records(fixture_records)


@pytest.fixture(scope="module")
def parsed_dir(tmp_path_factory, fixture_records):
    directory = tmp_path_factory.mktemp("parsed")
    for record in fixture_records:
        write_parsed(record, directory)
    return directory


# ── ChunkIndex ────────────────────────────────────────────────────────────────

def test_index_holds_every_chunk_of_every_fixture(index, fixture_records):
    expected = [c.chunk_id for r in fixture_records for c in chunk_filing(r.filing)]
    assert sorted(index.chunk_ids()) == sorted(expected)


def test_index_text_is_the_exact_source_slice(index, fixture_records):
    record = fixture_records[0]
    chunk = chunk_filing(record.filing)[3]
    assert index.text(chunk.chunk_id) == record.filing.text[chunk.char_start : chunk.char_end]


def test_index_maps_cik_to_ticker(index, fixture_records):
    tickers = {index.ticker(c) for c in index.chunk_ids()}
    assert tickers == {"NVDA", "AMD", "INTC"}


def test_unknown_chunk_is_none(index):
    assert index.text("0000000000-00-000000:0000") is None


def test_search_ranks_chunks_containing_all_terms(index):
    hits = index.search("foundry capacity", ticker="INTC", limit=5)
    assert hits and all(index.ticker(h.chunk_id) == "INTC" for h in hits)
    top = index.text(hits[0].chunk_id).lower()
    assert "foundry" in top and "capacity" in top


def test_search_filters_by_period_and_section(index):
    hits = index.search("risk", section="part_i_item_1a", limit=50)
    assert hits and all(index.section(h.chunk_id) == "part_i_item_1a" for h in hits)


# ── validate ──────────────────────────────────────────────────────────────────

def test_valid_set_has_no_problems(index):
    assert validate([make_record(index)], index, human_set=True) == []


def test_unknown_gold_chunk_is_a_problem(index):
    record = make_record(index)
    bad = record.model_copy(
        update={
            "gold_chunk_ids": ["0000000000-00-000000:0000"],
            "gold_text_sha256": {"0000000000-00-000000:0000": "0" * 64},
        }
    )
    problems = validate([bad], index, human_set=True)
    assert any("does not resolve" in p for p in problems)


def test_changed_chunk_text_is_a_problem(index):
    record = make_record(index)
    gold = record.gold_chunk_ids[0]
    bad = record.model_copy(update={"gold_text_sha256": {gold: "0" * 64}})
    assert any("text changed" in p for p in validate([bad], index, human_set=True))


def test_answerable_record_needs_a_hard_negative(index):
    record = make_record(index, hard_negatives=[])
    assert any("hard negative" in p for p in validate([record], index, human_set=True))


def test_unknown_hard_negative_is_a_problem(index):
    record = make_record(
        index,
        hard_negatives=[{"chunk_id": "0000000000-00-000000:0001", "relation": "peer_company"}],
    )
    assert any("hard negative" in p for p in validate([record], index, human_set=True))


def test_ticker_label_must_match_the_gold_chunks(index):
    record = make_record(index)
    other = next(t for t in ("NVDA", "AMD", "INTC") if t not in record.tickers)
    bad = record.model_copy(update={"tickers": [other]})
    assert any("tickers" in p for p in validate([bad], index, human_set=True))


def test_agent_drafted_record_is_refused_in_the_human_set(index):
    record = make_record(index, provenance="agent_drafted", author="claude-subagent")
    assert any("agent_drafted" in p for p in validate([record], index, human_set=True))
    assert validate([record], index, human_set=False) == []


def test_human_provenance_is_refused_in_the_drafted_set(index):
    record = make_record(index, provenance="human_verified", derived_from="0" * 16)
    assert any("drafted set" in p for p in validate([record], index, human_set=False))


def test_validate_cli_kind_overrides_the_file_name(tmp_path, parsed_dir, index, capsys):
    path = tmp_path / "my_human_set.jsonl"
    dump_records([make_record(index)], path)
    args = [str(path), "--parsed-dir", str(parsed_dir), "--review-log", str(tmp_path / "log")]
    assert validate_main([*args, "--kind", "human"]) == 0
    assert validate_main(args) == 1  # by name it is a drafted set


def test_coverage_cli_with_a_bad_file_fails_cleanly(tmp_path, capsys):
    from eval.coverage import main as coverage_main

    bad = tmp_path / "set.jsonl"
    bad.write_text("{not json}\n")
    assert coverage_main([str(bad)]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_validate_cli_passes_and_fails_with_exit_codes(tmp_path, parsed_dir, index, capsys):
    good = tmp_path / "eval_set.jsonl"
    dump_records([make_record(index)], good)
    assert validate_main([str(good), "--parsed-dir", str(parsed_dir)]) == 0
    drafted = make_record(index, provenance="agent_drafted", author="claude-subagent")
    dump_records([drafted], good)
    assert validate_main([str(good), "--parsed-dir", str(parsed_dir)]) == 1
    assert "agent_drafted" in capsys.readouterr().out


# ── coverage ──────────────────────────────────────────────────────────────────

def test_coverage_counts_by_class_ticker_and_split(index):
    records = [
        make_record(index, id="q0001"),
        make_record(index, id="q0002", question="Which split is this?", split="test"),
        make_record(
            index, id="q0003", gold_chunk_ids=[], gold_text_sha256={}, **{"class": "decline"}
        ),
    ]
    report = coverage(records)
    assert report.by_class["lookup"] == 2 and report.by_class["decline"] == 1
    assert report.by_split["test"] >= 1 and sum(report.by_split.values()) == 3
    text = format_coverage(report)
    assert f"| lookup | 2 | {TARGETS['lookup']} |" in text


# ── search CLI ────────────────────────────────────────────────────────────────

def test_search_cli_prints_ids_and_show_prints_exact_text(parsed_dir, index, capsys):
    assert search_main(["foundry", "--ticker", "INTC", "--parsed-dir", str(parsed_dir)]) == 0
    out = capsys.readouterr().out
    chunk_id = out.split()[0]
    assert index.ticker(chunk_id) == "INTC"
    assert search_main(["--show", chunk_id, "--parsed-dir", str(parsed_dir)]) == 0
    assert index.text(chunk_id) in capsys.readouterr().out


# ── validate: review findings (Step 4 python-review) ─────────────────────────

def _other_period_chunk(index, gold_id):
    gold = index.info(gold_id)
    return next(
        c for c in index.chunk_ids()
        if index.info(c).ticker == gold.ticker and index.info(c).fiscal_period != gold.fiscal_period
    )


def _peer_chunk(index, gold_id):
    gold = index.info(gold_id)
    return next(c for c in index.chunk_ids() if index.info(c).ticker != gold.ticker)


def test_duplicate_ids_are_a_problem(index):
    a = make_record(index)
    b = make_record(index, question="A different question about margins?")
    problems = validate([a, b], index, human_set=True)
    assert any("duplicate id q0001" in p for p in problems)


def test_split_must_follow_the_question_hash(index):
    record = make_record(index)
    wrong = "test" if record.split == "dev" else "dev"
    bad = record.model_copy(update={"split": wrong})
    assert any("split" in p for p in validate([bad], index, human_set=True))


@pytest.mark.parametrize("cls,n_gold", [("multi_hop", 1), ("lookup", 2), ("global", 2)])
def test_class_must_fit_its_evidence_scope(index, cls, n_gold):
    gold = index.chunk_ids()[:n_gold]
    scope = {1: "single", 2: "two"}[n_gold]
    record = make_record(index, gold_chunk_ids=gold, evidence_scope=scope, **{"class": cls})
    record = record.model_copy(
        update={"tickers": sorted({index.ticker(c) for c in gold}),
                "fiscal_periods": sorted({index.fiscal_period(c) for c in gold})}
    )
    assert any("class" in p for p in validate([record], index, human_set=True))


def test_author_must_fit_the_provenance(index):
    drafted = make_record(index, provenance="agent_drafted", author="rohan")
    assert any("author" in p for p in validate([drafted], index, human_set=False))
    human = make_record(index, author="claude-subagent:x")
    assert any("author" in p for p in validate([human], index, human_set=True))


def test_correct_hard_negative_relations_pass(index):
    gold = index.chunk_ids()[0]
    negs = [
        {"chunk_id": _other_period_chunk(index, gold), "relation": "same_company_other_period"},
        {"chunk_id": _peer_chunk(index, gold), "relation": "peer_company"},
    ]
    assert validate([make_record(index, hard_negatives=negs)], index, human_set=True) == []


def test_wrong_hard_negative_relation_is_a_problem(index):
    gold = index.chunk_ids()[0]
    neg = {"chunk_id": _other_period_chunk(index, gold), "relation": "peer_company"}
    problems = validate([make_record(index, hard_negatives=[neg])], index, human_set=True)
    assert any("is not peer_company" in p for p in problems)


def test_human_verified_needs_a_logged_acceptance(index):
    record = make_record(index, provenance="human_verified", derived_from="a" * 16)
    assert validate([record], index, human_set=True, accepted_keys={"a" * 16}) == []
    problems = validate([record], index, human_set=True, accepted_keys=set())
    assert any("review log" in p for p in problems)


# ── ChunkIndex cache ──────────────────────────────────────────────────────────

def test_corrupt_cache_is_rebuilt(parsed_dir):
    from eval.corpus_index import CACHE_NAME

    first = ChunkIndex.from_parsed_dir(parsed_dir)
    (parsed_dir / CACHE_NAME).write_text('{"key": [1, 2], "chunks": 5}')
    again = ChunkIndex.from_parsed_dir(parsed_dir)
    assert again.chunk_ids() == first.chunk_ids()


def test_cache_shaped_wrongly_is_rebuilt(parsed_dir):
    from eval.corpus_index import CACHE_NAME

    first = ChunkIndex.from_parsed_dir(parsed_dir)
    (parsed_dir / CACHE_NAME).write_text("[]")
    assert ChunkIndex.from_parsed_dir(parsed_dir).chunk_ids() == first.chunk_ids()


def test_search_with_a_missing_parsed_dir_fails_cleanly(tmp_path, capsys):
    assert search_main(["revenue", "--parsed-dir", str(tmp_path / "absent")]) == 2
    assert "absent" in capsys.readouterr().err
