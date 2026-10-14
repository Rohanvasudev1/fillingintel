"""``python -m retrieve.node_embed``: the estimate first, embedding only when asked
(Step 11 ticket 05).  Offline: the nodes and the Voyage client are fakes."""
from contextlib import nullcontext

import pytest

from retrieve.anchors import AnchorNode
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


def _main(tmp_path, voyage, *args: str) -> int:
    return main(list(args), read_nodes=lambda: NODES, open_voyage=lambda: nullcontext(voyage),
                cache_dir=tmp_path / "cache")


def test_by_default_it_prints_the_estimate_and_embeds_nothing(tmp_path, voyage, capsys):
    assert _main(tmp_path, voyage) == EXIT_OK
    out = capsys.readouterr().out
    assert "4 nodes, 3 distinct texts, 3 not cached" in out
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
