"""``python -m eval.results_table`` on the real committed dev run files and reports
(Step 13 ticket 02).  ``test`` behaviour is checked by passing the dev files in the
``test`` role."""
import hashlib
import json

import pytest

from eval.compare import compare_paired
from eval.results_inputs import DEV_RUNS, RESULTS_MD
from eval.results_table import EXIT_DIFFERS, EXIT_OK, build, main
from ingest.provenance import REPO_ROOT

RUNS = REPO_ROOT / "benchmarks" / "runs"
VECTOR, LOCAL = DEV_RUNS["vector"][0], DEV_RUNS["graph_local"][0]


@pytest.fixture(scope="module")
def text() -> str:
    return build()


def _rows(text: str, *starts: str) -> list[str]:
    prefix = "| " + " | ".join(starts) + " |"
    return [line for line in text.splitlines() if line.startswith(prefix)]


def test_the_build_is_byte_identical_across_runs(text):
    assert build() == text


def test_the_header_lists_every_input_with_its_sha256_and_states_the_question_source(text):
    for name in (n for names in DEV_RUNS.values() for n in names):
        sha = hashlib.sha256((RUNS / name).read_bytes()).hexdigest()
        assert f"| `benchmarks/runs/{name}` | `{sha}` |" in text
    head = text.split("## ")[0]
    assert "agent-drafted questions" in head


def test_every_arm_has_a_row_in_every_answerable_class_with_intervals(text):
    for class_ in ("lookup", "local", "multi_hop", "global"):
        for arm in ("vector", "graph_local", "graph_traversal", "graph_global"):
            (row,) = _rows(text.split("## `dev` results")[1], class_, arm)
            assert row.count("[") >= 4  # recall@5, recall@10, precision@10, wrong evidence


def test_retrieval_only_cells_say_so(text):
    dev = text.split("## `dev` results")[1]
    (row,) = _rows(dev, "lookup", "graph_global")
    assert row.count("retrieval only") == 6  # three judged, two latency, cost
    assert "(ranks only)" in row
    (full,) = _rows(dev, "global", "graph_global")
    assert "retrieval only" not in full
    assert "0.458 [0.300, 0.617]" in full  # Step 12's global recall@10


def test_paired_differences_equal_eval_compares(text):
    before, after = (json.loads((RUNS / n).read_text()) for n in (VECTOR, LOCAL))
    expected = next(d for d in compare_paired(before, after).differences
                    if d.class_ == "lookup" and d.metric == "metrics.recall@10")
    (row,) = _rows(text.split("### `dev` paired differences")[1],
                   "graph_local", "lookup", "recall@10")
    assert f"{expected.mean:.3f} [{expected.low:.3f}, {expected.high:.3f}]" in row
    (decline,) = _rows(text.split("### `dev` paired differences")[1],
                       "graph_local", "decline")
    assert "directional" in decline


def test_hypothesis_verdicts_print_their_rules_and_readings(text):
    section = text.split("## Hypotheses")[1].split("## Router")[0]
    for name in ("H1", "H2", "H3", "H4", "H5"):
        assert f": {name}, " in section
    assert "graph_global - vector: mean -0.088 [-0.160, -0.027], n 10" in section  # H4
    assert "**not testable**: H5" in section  # no total_ms before Step 13


def test_the_router_section_holds_the_dev_best_map(text):
    section = text.split("### Dev-best routing map")[1].split("## Other measured")[0]
    (row,) = _rows(section, "global", "vector")
    assert row.endswith("| **yes** |")


def test_test_runs_go_on_top(tmp_path):
    text = build(test_runs=DEV_RUNS)
    assert text.index("## `test` results") < text.index("## Hypotheses (read on `test`)")
    assert text.index("## Hypotheses") < text.index("## `dev` results")


def test_check_exits_0_on_a_match_and_4_on_a_one_character_difference(tmp_path, capsys):
    output = tmp_path / "results.md"
    assert main(["--output", str(output)]) == EXIT_OK
    assert main(["--check", "--output", str(output)]) == EXIT_OK
    output.write_text(output.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert main(["--check", "--output", str(output)]) == EXIT_DIFFERS
    assert "differs" in capsys.readouterr().err


def test_the_committed_results_file_matches_the_committed_runs():
    assert main(["--check", "--output", str(REPO_ROOT / RESULTS_MD)]) == EXIT_OK
