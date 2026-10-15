"""``python -m retrieve.node_embed``: the estimate first, embedding only when asked
(Step 11 ticket 05).  Offline: the nodes and the Voyage client are fakes."""
from contextlib import nullcontext
from types import MappingProxyType

import pytest

from graph.report_file import ReportEntry, ReportsFile
from retrieve.anchors import AnchorNode
from retrieve.arm import ArmError
from retrieve.community_select import report_texts
from retrieve.node_embed import EXIT_COULD_NOT_RUN, EXIT_OK, main
from retrieve.query_cache import CachedBatchEmbedder
from tests.graph_local_data import FakeNodeEmbedder

NODES = (
    AnchorNode("Product:blackwell", "Product", "Product: Blackwell", ("Blackwell",),
               frozenset({"a"})),
    AnchorNode("Product:hopper", "Product", "Product: Hopper", ("Hopper",), frozenset({"a"})),
    AnchorNode("rf:1", "RiskFactor", "RiskFactor: Demand may fall", (), frozenset({"a"})),
    AnchorNode("rf:2", "RiskFactor", "RiskFactor: Demand may fall", (), frozenset({"b"})),
)


@pytest.fixture
def voyage():
    return FakeNodeEmbedder({})


def _main(tmp_path, voyage, *args: str, reports=()) -> int:
    return main(list(args), read_nodes=lambda: NODES, read_reports=lambda: reports,
                open_voyage=lambda: nullcontext(voyage), cache_dir=tmp_path / "cache")


def test_by_default_it_prints_the_estimate_and_embeds_nothing(tmp_path, voyage, capsys):
    assert _main(tmp_path, voyage) == EXIT_OK
    out = capsys.readouterr().out
    assert "4 nodes and 0 community reports, 3 distinct texts, 3 not cached" in out
    assert "estimate" in out and "$" in out
    assert "--embed" in out
    assert voyage.texts == []


def test_embed_sends_only_the_texts_not_cached(tmp_path, voyage, capsys):
    CachedBatchEmbedder(voyage, cache_dir=tmp_path / "cache").embed(
        ["Product: Hopper"], "voyage-4-large", "document")
    voyage.texts.clear()
    assert _main(tmp_path, voyage, "--embed") == EXIT_OK
    assert voyage.texts == ["Product: Blackwell", "RiskFactor: Demand may fall"]
    assert "Voyage reported 10 tokens" in capsys.readouterr().out


def test_a_second_run_finds_everything_cached(tmp_path, voyage, capsys):
    _main(tmp_path, voyage, "--embed")
    voyage.texts.clear()
    assert _main(tmp_path, voyage, "--embed") == EXIT_OK
    assert "0 not cached" in capsys.readouterr().out
    assert voyage.texts == []


def test_embed_refuses_an_estimate_above_the_cap(tmp_path, voyage, capsys):
    assert _main(tmp_path, voyage, "--embed", "--max-cost", "0") == EXIT_COULD_NOT_RUN
    assert "above --max-cost" in capsys.readouterr().err
    assert voyage.texts == []


REPORTS = ("TSMC and foundries: NVIDIA relies on TSMC [a:0001].",
           "Export controls: Sales to China are restricted [a:0002].")


def test_community_report_texts_are_embedded_with_the_node_texts(tmp_path, voyage, capsys):
    assert _main(tmp_path, voyage, "--embed", reports=REPORTS) == EXIT_OK
    assert "4 nodes and 2 community reports, 5 distinct texts, 5 not cached" in \
        capsys.readouterr().out
    assert voyage.texts[-2:] == list(REPORTS)


def test_unreadable_reports_stop_the_run(tmp_path, voyage, capsys):
    def broken():
        raise ArmError("no graph load report")

    code = main([], read_nodes=lambda: NODES, read_reports=broken,
                open_voyage=lambda: nullcontext(voyage), cache_dir=tmp_path / "cache")
    assert code == EXIT_COULD_NOT_RUN
    assert "no graph load report" in capsys.readouterr().err


def test_report_texts_are_title_then_summary_for_every_report_that_kept_text():
    entries = {
        "lower-a": ReportEntry("lower-a", "h" * 64, "lower", "T1", "S1 [a:0001].",
                               ("a:0001",), "report/v2@3062cfff", "S1 [a:0001]."),
        "top-b": ReportEntry("top-b", "h" * 64, "top", "T2", "S2 [a:0001].", ("a:0001",),
                             "report/v2@3062cfff", "S2 [a:0001]."),
        "lower-empty": ReportEntry("lower-empty", "h" * 64, "lower", "T3", "", (),
                                   "report/v2@3062cfff", ""),
    }
    reports = ReportsFile("reports-run2.jsonl", "r" * 64, "communities-run1.json", "c" * 64,
                          "a" * 64, True, MappingProxyType(entries))
    assert report_texts(reports) == ["T1: S1 [a:0001].", "T2: S2 [a:0001]."]
