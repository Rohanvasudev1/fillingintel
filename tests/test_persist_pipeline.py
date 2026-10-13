"""link_factors(): one persistence pass with fake embeddings and a scripted adjudicator
(Step 10)."""
from datetime import date

import pytest

from persist.config import PersistConfig
from persist.decisions import Answer
from persist.filings import Filing
from persist.links import EXACT_TITLE
from persist.pipeline import link_factors
from tests.persist_helpers import (
    AMD_10K,
    AMD_10Q,
    CLIMATE,
    CURRENCY,
    FILINGS,
    GUARANTEES_10K,
    GUARANTEES_10Q,
    LITIGATION_10Q,
    SEASONAL,
    ScriptedAdjudicator,
    earlier,
    factor,
    later,
)

OLD = (earlier(SEASONAL, 21), earlier(GUARANTEES_10K, 53), earlier(CURRENCY, 28),
       earlier(CLIMATE, 41))
EXACT = later(CURRENCY, 50)
NEW_GUARANTEES = later(GUARANTEES_10Q, 78)
NEW_LITIGATION = later(LITIGATION_10Q, 71)
NEW = (EXACT, NEW_GUARANTEES, NEW_LITIGATION)
# Each later factor sits nearest the earlier factor at the same position in this order.
DIRECTIONS = {SEASONAL: (1, 0, 0, 0), GUARANTEES_10K: (0, 1, 0, 0), CURRENCY: (0, 0, 1, 0),
              CLIMATE: (0, 0, 0, 1)}
NEAR = {GUARANTEES_10Q: (0.1, 1, 0, 0.2), LITIGATION_10Q: (0.3, 0.1, 0, 1), CURRENCY: (0, 0, 1, 0)}


class FakeEmbedder:
    """A fixed vector per factor title (the text starts with it); records the texts."""

    def __init__(self):
        self.texts = []

    def embed(self, texts):
        self.texts += texts
        vectors = {**DIRECTIONS, **NEAR}
        return [vectors[next(t for t in vectors if text.startswith(t + ":"))] for text in texts]


def _run(adjudicator=None, embedder=None, factors=(*OLD, *NEW), filings=FILINGS, top_k=3):
    return link_factors(filings, factors, embedder or FakeEmbedder(), adjudicator,
                        PersistConfig(top_k=top_k))


def test_an_exact_title_links_with_no_call_and_is_not_embedded():
    adjudicator, embedder = ScriptedAdjudicator(), FakeEmbedder()
    result = _run(adjudicator, embedder)
    (exact,) = [link for link in result.links if link.method == EXACT_TITLE]
    assert (exact.later_key, exact.earlier_key) == (EXACT.key, OLD[2].key)
    assert all(name != CURRENCY for name, _ in adjudicator.judged)
    assert sum(t.startswith(CURRENCY + ":") for t in embedder.texts) == 1  # the earlier one


def test_every_other_later_factor_sends_its_top_3_earlier_neighbours():
    adjudicator = ScriptedAdjudicator()
    result = _run(adjudicator)
    assert sorted(adjudicator.judged) == sorted(
        [(GUARANTEES_10Q, GUARANTEES_10K), (GUARANTEES_10Q, CLIMATE), (GUARANTEES_10Q, SEASONAL),
         (LITIGATION_10Q, CLIMATE), (LITIGATION_10Q, SEASONAL),
         (LITIGATION_10Q, GUARANTEES_10K)])
    ranks = {(c.later_key, c.earlier_key): c.rank for c in result.candidates}
    assert ranks[(NEW_GUARANTEES.key, OLD[1].key)] == 1
    assert len(result.decisions) == 6


def test_a_verified_same_links_with_the_prompt_version_as_method():
    result = _run(ScriptedAdjudicator({(GUARANTEES_10Q, GUARANTEES_10K): Answer.SAME}))
    by_later = {link.later_key: link for link in result.links}
    assert by_later[NEW_GUARANTEES.key].earlier_key == OLD[1].key
    assert by_later[NEW_GUARANTEES.key].method == ScriptedAdjudicator.prompt_version
    assert NEW_LITIGATION.key not in by_later


def test_the_highest_ranked_same_wins_when_two_neighbours_are_same():
    answers = {(GUARANTEES_10Q, GUARANTEES_10K): Answer.SAME,
               (GUARANTEES_10Q, CLIMATE): Answer.SAME}
    (link,) = [lk for lk in _run(ScriptedAdjudicator(answers)).links
               if lk.later_key == NEW_GUARANTEES.key]
    assert (link.earlier_key, link.rank) == (OLD[1].key, 1)


def test_without_an_adjudicator_the_candidates_are_pending_and_only_exact_links_come_back():
    result = _run(None)
    assert len(result.pending) == 6 and result.decisions == ()
    assert [link.method for link in result.links] == [EXACT_TITLE]


def test_a_filing_with_no_earlier_10k_gets_no_links_and_no_calls():
    filings = (Filing(AMD_10K, "2488", "10-K", date(2025, 12, 27)),)
    adjudicator = ScriptedAdjudicator()
    result = link_factors(filings, OLD, FakeEmbedder(), adjudicator, PersistConfig())
    assert result.links == () and adjudicator.judged == [] and result.pairs == ()


def test_each_pairs_counts_add_up():
    result = _run(ScriptedAdjudicator({(GUARANTEES_10Q, GUARANTEES_10K): Answer.SAME}))
    (counts,) = result.pair_counts
    assert (counts.later_factors, counts.earlier_factors, counts.exact, counts.candidates,
            counts.linked_exact, counts.linked_adjudicated) == (3, 4, 1, 6, 1, 1)


def test_a_risk_factor_from_a_filing_outside_the_list_is_refused():
    stray = factor("Stray", "0000002488-99-000001", 1)
    with pytest.raises(ValueError, match="0000002488-99-000001"):
        _run(factors=(*OLD, stray))


def test_a_node_that_is_not_a_risk_factor_is_refused():
    from resolve.nodes import EvidenceRef, ResolveNode

    org = ResolveNode("Organization:x", "Organization", "X",
                      (EvidenceRef(f"{AMD_10K}:0001", "X"),))
    with pytest.raises(ValueError, match="RiskFactor"):
        _run(factors=(*OLD, org))


def test_a_risk_factor_with_evidence_from_two_filings_is_refused():
    from resolve.nodes import EvidenceRef, ResolveNode

    mixed = ResolveNode(f"{AMD_10K}:x", "RiskFactor", "X",
                        (EvidenceRef(f"{AMD_10K}:0001", "X"), EvidenceRef(f"{AMD_10Q}:0001", "X")))
    with pytest.raises(ValueError, match="filings"):
        _run(factors=(*OLD, mixed))


def test_top_k_comes_from_the_config():
    adjudicator = ScriptedAdjudicator()
    _run(adjudicator, top_k=1)
    assert sorted(adjudicator.judged) == sorted([(GUARANTEES_10Q, GUARANTEES_10K),
                                                 (LITIGATION_10Q, CLIMATE)])
