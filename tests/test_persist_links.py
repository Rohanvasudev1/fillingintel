"""Persistence links: one predecessor per later risk factor, and each link's evidence (Step 10)."""
from dataclasses import replace

import pytest

from graph.batch import Batch, NodeRef, check_batch
from persist.candidates import CandidatePair
from persist.decisions import Answer, LinkEvidence
from persist.links import (
    EXACT_TITLE,
    PersistLink,
    check_links,
    decision_link,
    exact_link,
    select_links,
)
from persist.paths import read_links, write_links
from tests.persist_helpers import (
    GUARANTEES_10K,
    GUARANTEES_10Q,
    SEASONAL,
    ScriptedAdjudicator,
    earlier,
    later,
)

OLD_SEASONAL = earlier(SEASONAL, 21, 27)
OLD_GUARANTEES = earlier(GUARANTEES_10K, 53)
OLD_SUMMARY = earlier("We may be required to satisfy financial obligations under guarantees and "
                      "other commercial commitments", 22)
NEW_SEASONAL = later(SEASONAL + ".", 49)
NEW_GUARANTEES = later(GUARANTEES_10Q, 78)


def _decision(new, old, rank, answer=Answer.SAME, verified=True):
    adjudicator = ScriptedAdjudicator({(new.name, old.name): answer}, verified=verified)
    return adjudicator.judge(CandidatePair(new.key, old.key, rank, 0.9), new, old)


def _check(link):
    refs = frozenset(NodeRef.of("RiskFactor", key=k) for k in (link.earlier_key, link.later_key))
    return check_batch(Batch(edges=(link.edge(),)),
                       existing_chunk_ids=frozenset(link.edge().properties["chunk_ids"]),
                       existing_nodes=refs)


def test_an_exact_title_link_carries_each_factors_own_first_span_earlier_first():
    link = exact_link(NEW_SEASONAL, OLD_SEASONAL)
    edge = link.edge()
    assert (edge.type, edge.start, edge.end) == (
        "PERSISTS_AS", NodeRef.of("RiskFactor", key=OLD_SEASONAL.key),
        NodeRef.of("RiskFactor", key=NEW_SEASONAL.key))
    assert dict(edge.properties) == {
        "chunk_ids": [OLD_SEASONAL.evidence[0].chunk_id, NEW_SEASONAL.evidence[0].chunk_id],
        "evidence_spans": [OLD_SEASONAL.evidence[0].span, NEW_SEASONAL.evidence[0].span],
        "confidences": ["stated", "stated"],
        "extract_prompts": [EXACT_TITLE, EXACT_TITLE],
    }
    assert link.method == "persist/exact_title" and link.decision_id is None
    assert _check(link) == ()


def test_an_adjudicated_link_carries_the_verified_quotes_and_the_prompt_version():
    decision = replace(_decision(NEW_GUARANTEES, OLD_GUARANTEES, 2),
                       verified_earlier=LinkEvidence(OLD_GUARANTEES.chunk_ids[0], "quoted earlier"),
                       verified_later=LinkEvidence(NEW_GUARANTEES.chunk_ids[0], "quoted later"))
    link = decision_link(decision)
    props = link.edge().properties
    assert props["chunk_ids"] == [decision.chunk_id_earlier, decision.chunk_id_later]
    assert props["evidence_spans"] == ["quoted earlier", "quoted later"]
    assert props["extract_prompts"] == [decision.prompt_version] * 2
    assert (link.decision_id, link.rank) == (decision.decision_id, 2)
    assert _check(link) == ()


def test_a_decision_that_does_not_link_cannot_become_a_link():
    with pytest.raises(ValueError, match="does not link"):
        decision_link(_decision(NEW_GUARANTEES, OLD_GUARANTEES, 1, Answer.UNSURE))


def test_an_exact_match_wins_over_any_same_decision():
    exact = exact_link(NEW_SEASONAL, OLD_SEASONAL)
    same = _decision(NEW_SEASONAL, OLD_GUARANTEES, 1)
    assert select_links([exact], [same]) == (exact,)


def test_the_highest_ranked_same_is_the_one_predecessor():
    decisions = [_decision(NEW_GUARANTEES, OLD_SUMMARY, 2),
                 _decision(NEW_GUARANTEES, OLD_GUARANTEES, 1),
                 _decision(NEW_GUARANTEES, OLD_SEASONAL, 3)]
    (link,) = select_links([], decisions)
    assert (link.earlier_key, link.rank) == (OLD_GUARANTEES.key, 1)


@pytest.mark.parametrize(("answer", "verified"), [
    (Answer.DISTINCT, True), (Answer.UNSURE, True), (Answer.SAME, False)])
def test_only_a_verified_same_links(answer, verified):
    assert select_links([], [_decision(NEW_GUARANTEES, OLD_GUARANTEES, 1, answer,
                                       verified)]) == ()


def test_an_earlier_factor_may_have_several_successors():
    links = select_links([exact_link(later(GUARANTEES_10K, 78), OLD_GUARANTEES)],
                         [_decision(NEW_GUARANTEES, OLD_GUARANTEES, 1)])
    assert [link.earlier_key for link in links] == [OLD_GUARANTEES.key] * 2
    assert len({link.later_key for link in links}) == 2


def test_a_link_survives_its_file_form():
    link = decision_link(_decision(NEW_GUARANTEES, OLD_GUARANTEES, 1))
    assert PersistLink.from_dict(link.to_dict()) == link


def test_a_malformed_link_line_is_refused():
    data = exact_link(NEW_SEASONAL, OLD_SEASONAL).to_dict()
    with pytest.raises(ValueError, match="link"):
        PersistLink.from_dict({**data, "earlier": {"chunk_id": 3}})


def test_valid_links_pass_the_write_paths_check():
    links = select_links([exact_link(NEW_SEASONAL, OLD_SEASONAL)],
                         [_decision(NEW_GUARANTEES, OLD_GUARANTEES, 1)])
    assert check_links(links) == ()


def test_a_link_with_a_blank_span_fails_the_write_paths_check():
    link = exact_link(NEW_SEASONAL, OLD_SEASONAL)
    broken = replace(link, later=replace(link.later, span=" "))
    (violation,) = check_links([broken])
    assert "PERSISTS_AS" in violation.item


def test_the_link_file_round_trips_and_is_sorted(tmp_path):
    links = select_links([exact_link(NEW_SEASONAL, OLD_SEASONAL)],
                         [_decision(NEW_GUARANTEES, OLD_GUARANTEES, 1)])
    path = tmp_path / "links.jsonl"
    write_links(path, reversed(links))
    assert read_links(path) == links
    assert path.read_text(encoding="utf-8").count("\n") == 2


def test_a_link_file_with_a_bad_line_is_refused_with_its_number(tmp_path):
    path = tmp_path / "links.jsonl"
    path.write_text('{"earlier_key": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        read_links(path)
