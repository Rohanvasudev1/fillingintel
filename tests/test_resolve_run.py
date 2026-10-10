"""`python -m resolve.run` and load_nodes(): files, reruns, cache and exit codes (Step 9).

Candidates files are written in extract.run's line format, with names and
chunk IDs from NVDA run 1 and one AMD filing.
"""
import hashlib
import json
from contextlib import nullcontext

import pytest

from ingest.voyage import BatchEmbedding
from resolve.config import ResolveConfig
from resolve.log import read_log, replay
from resolve.nodes import InputError, load_nodes
from resolve.paths import read_alias_map
from resolve.run import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE, main
from tests.resolve_helpers import AMD_10K, NVDA_10K

DIMENSIONS = 1024


def _evidence(chunk_id, span):
    return [{"chunk_id": chunk_id, "confidence": "stated", "extract_prompt": "extract/v1@00000000",
             "span": span}]


def _node(label, name, ordinal, accession_no=NVDA_10K):
    key = f"{label}:{name.casefold()}"
    return {"kind": "node", "label": label, "item": f"{label}(key={key!r})",
            "properties": {"key": key, "name": name},
            "evidence": _evidence(f"{accession_no}:{ordinal:04d}", name)}


def _metric(concept, ordinal, accession_no=NVDA_10K):
    key = f"{accession_no}:1045810:{concept.casefold()}:FY2026"
    node = {"kind": "node", "label": "MetricValue", "item": f"MetricValue(key={key!r})",
            "properties": {"key": key, "concept": concept, "value": 216.0,
                           "unit": "USD billions", "period": "FY2026"},
            "evidence": _evidence(f"{accession_no}:{ordinal:04d}", concept)}
    edge = {"kind": "edge", "type": "MEASURES", "item": "...",
            "start": {"label": "MetricValue", "key": {"key": key}},
            "end": {"label": "Company", "key": {"cik": "1045810"}},
            "properties": {}}
    return [node, edge]


NVDA_LINES = [
    _node("Organization", "Groq", 30), _node("Organization", "Groq, Inc.", 31),
    _node("Regulation", "GDPR", 50), _node("Regulation", "General Data Protection Regulation", 51),
    *_metric("Revenue", 61), *_metric("Total revenue", 62),
    {"kind": "flag", "chunk_id": f"{NVDA_10K}:0001", "reason": "name_not_in_span", "item": "x"},
]
AMD_LINES = [_node("Organization", "NVIDIA Corporation", 40, AMD_10K),
             _node("Organization", "Groq", 41, AMD_10K)]


def _write(directory, name, lines):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


@pytest.fixture
def candidates(tmp_path):
    directory = tmp_path / "extract"
    _write(directory, f"{NVDA_10K}-run1.jsonl", NVDA_LINES[:2])  # an older run, not read
    _write(directory, f"{NVDA_10K}-run2.jsonl", NVDA_LINES)
    _write(directory, f"{AMD_10K}-run1.jsonl", AMD_LINES)
    return directory


class FakeVoyage:
    """A BatchEmbedder with a vector per text from its hash; counts calls."""

    def __init__(self):
        self.calls = 0

    def embed_batch(self, texts, model, input_type):
        self.calls += 1
        vectors = []
        for text in texts:
            seed = hashlib.sha256(text.encode()).digest()
            vectors.append(tuple((seed[i % 32] - 128) / 128 for i in range(DIMENSIONS)))
        return BatchEmbedding(vectors=tuple(vectors), total_tokens=5 * len(texts))


def _never():
    raise AssertionError("no Voyage client should be opened")


def _main(tmp_path, candidates, argv=(), open_client=_never, config=ResolveConfig(top_k=0)):
    return main(list(argv), candidates_dir=candidates, out_dir=tmp_path / "out",
                embed_cache=tmp_path / "cache", open_client=open_client, config=config)


def _report(tmp_path, n):
    return json.loads((tmp_path / "out" / f"run{n}.json").read_text(encoding="utf-8"))


def test_a_run_writes_the_log_the_alias_map_and_the_report(tmp_path, candidates):
    assert _main(tmp_path, candidates) == EXIT_OK
    out = tmp_path / "out"
    aliases = read_alias_map(out / "alias_map.json")
    assert aliases == replay(read_log(out / "merge_log.jsonl"))
    assert aliases["Organization:groq, inc."] == "Organization:groq"
    assert aliases["Organization:nvidia corporation"] == "Company:1045810"
    report = _report(tmp_path, 1)
    assert [i["file"] for i in report["provenance"]["inputs"]] == [
        f"{AMD_10K}-run1.jsonl", f"{NVDA_10K}-run2.jsonl"]
    assert report["provenance"]["config_hash"] == ResolveConfig(top_k=0).config_hash
    assert report["counts"]["merges"] == {"proposed": 2, "new": 2, "by_rule": {
        "filer_table": 1, "normalized_equal": 1}}
    assert report["counts"]["pending_by_label"] == {"MetricValue": 1, "Regulation": 1}
    assert report["adjudication"] == {} and report["labels_and_scoring"] == {}


def test_a_rerun_on_unchanged_inputs_appends_nothing(tmp_path, candidates):
    _main(tmp_path, candidates)
    log = tmp_path / "out" / "merge_log.jsonl"
    before = log.read_bytes()
    assert _main(tmp_path, candidates) == EXIT_OK
    assert log.read_bytes() == before
    assert _report(tmp_path, 2)["counts"]["merges"]["new"] == 0


def test_explicit_candidates_files_are_read_instead(tmp_path, candidates):
    only = candidates / f"{AMD_10K}-run1.jsonl"
    assert _main(tmp_path, candidates, ["--candidates", str(only)]) == EXIT_OK
    assert [i["file"] for i in _report(tmp_path, 1)["provenance"]["inputs"]] == [only.name]


def test_a_cached_run_makes_zero_calls(tmp_path, candidates):
    voyage = FakeVoyage()
    config = ResolveConfig()
    assert _main(tmp_path, candidates, open_client=lambda: nullcontext(voyage),
                 config=config) == EXIT_OK
    assert voyage.calls == 1
    assert _report(tmp_path, 1)["embedding"]["calls"] == 1
    assert _main(tmp_path, candidates, config=config) == EXIT_OK  # _never would raise
    assert _report(tmp_path, 2)["embedding"] == {**_report(tmp_path, 2)["embedding"],
                                                 "calls": 0, "tokens": 0, "cost_usd": 0.0}


def test_uncached_embeddings_without_a_key_cannot_run(tmp_path, candidates, monkeypatch,
                                                      capsys):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert main([], candidates_dir=candidates, out_dir=tmp_path / "out",
                embed_cache=tmp_path / "cache") == EXIT_RUN_ERROR
    assert "VOYAGE_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_no_candidates_files_is_bad_input(tmp_path):
    assert _main(tmp_path, tmp_path / "empty") == EXIT_USAGE


def test_bad_arguments_are_bad_input(tmp_path, candidates):
    assert _main(tmp_path, candidates, ["--nonsense"]) == EXIT_USAGE


def test_a_malformed_candidates_file_is_bad_input(tmp_path, candidates, capsys):
    (candidates / f"{AMD_10K}-run1.jsonl").write_text('{"kind": "node"}\n', encoding="utf-8")
    assert _main(tmp_path, candidates) == EXIT_USAGE
    assert "line 1" in capsys.readouterr().err


def test_an_alias_map_edited_by_hand_fails_the_check(tmp_path, candidates):
    _main(tmp_path, candidates)
    path = tmp_path / "out" / "alias_map.json"
    path.write_text(json.dumps({"Organization:groq": "Organization:broadcom"}), encoding="utf-8")
    assert _main(tmp_path, candidates) == EXIT_CHECK_FAILED


def test_a_malformed_merge_log_is_bad_input(tmp_path, candidates):
    out = tmp_path / "out"
    out.mkdir()
    (out / "merge_log.jsonl").write_text("not json\n", encoding="utf-8")
    assert _main(tmp_path, candidates) == EXIT_USAGE


# ── load_nodes ────────────────────────────────────────────────────────────────

def test_a_key_in_two_files_is_one_node_with_both_evidence(tmp_path, candidates):
    nodes, files = load_nodes(sorted(candidates.glob("*run1.jsonl")) +
                              [candidates / f"{NVDA_10K}-run2.jsonl"])
    (groq,) = [n for n in nodes if n.key == "Organization:groq"]
    assert groq.chunk_ids == (f"{AMD_10K}:0041", f"{NVDA_10K}:0030")
    assert len(files) == 3


def test_a_metric_value_takes_its_subject_from_its_measures_edge(tmp_path, candidates):
    nodes, _ = load_nodes([candidates / f"{NVDA_10K}-run2.jsonl"])
    (revenue,) = [n for n in nodes if n.name == "Revenue"]
    assert revenue.metric.subject_key == "1045810"
    assert revenue.metric.accession_no == NVDA_10K


def test_a_metric_value_without_a_measures_edge_is_refused(tmp_path):
    path = _write(tmp_path, f"{NVDA_10K}-run1.jsonl", _metric("Revenue", 61)[:1])
    with pytest.raises(InputError, match="MEASURES"):
        load_nodes([path])


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(InputError, match="cannot read"):
        load_nodes([tmp_path / "absent.jsonl"])
