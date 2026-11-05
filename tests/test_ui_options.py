"""The Ask view's picker options (Step 14 ticket 01)."""
import pytest

from ingest.provenance import REPO_ROOT
from retrieve.arms import ARMS
from ui.options import DEFAULT_OPTION, OPTIONS, UnknownOption, option_spec, picker_option


def test_the_six_options_in_picker_order_with_vector_first():
    assert [o.label for o in OPTIONS] == [
        "vector", "router (dev-best)", "router (pre-registered)", "graph_local",
        "graph_traversal", "graph_global"]
    assert DEFAULT_OPTION == OPTIONS[0].label
    assert picker_option("vector").note == "best measured arm"
    assert picker_option("router (dev-best)").note == "tuned on dev"


def test_routers_open_through_their_routing_map():
    dev_best = option_spec(picker_option("router (dev-best)"), ARMS)
    assert dev_best.name == "router"
    assert dev_best.with_map is None  # a mapped spec, as eval.run --map gives
    assert option_spec(picker_option("graph_local"), ARMS) is ARMS["graph_local"]


def test_every_option_names_committed_dev_runs():
    for option in OPTIONS:
        assert option.dev_runs
        for name in option.dev_runs:
            assert "-dev-" in name
            assert (REPO_ROOT / "benchmarks" / "runs" / name).is_file()


def test_an_unknown_option_is_refused_by_name():
    with pytest.raises(UnknownOption, match="unknown picker option 'bm25'"):
        picker_option("bm25")
