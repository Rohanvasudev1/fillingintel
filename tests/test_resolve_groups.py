"""resolve_nodes() with an adjudicator: groups of `same` decisions, held back or merged (Step 9).

The adjudicator is a fake that answers from a script, so these tests pin the
grouping rules alone: union-find over merging decisions, the canonical key, and
reruns. Cutting groups that hold a contradiction (Step 9b) is in
test_resolve_group_cut.py. The real
adjudicator's reply checks are in test_resolve_adjudicate.py.
"""
from resolve.config import ResolveConfig
from resolve.decisions import Answer, RelatedKind
from resolve.log import Method, Stamp
from resolve.pipeline import resolve_nodes
from tests.resolve_helpers import ADJUDICATOR_PROMPT, ScriptedAdjudicator, node

STAMP = Stamp(config_hash="c" * 64, commit="abc1234", timestamp="2026-10-10T12:00:00+00:00")
EVERY_PAIR = ResolveConfig(fuzzy_cutoff=0.0, top_k=0)  # every same-label pair is a candidate


def _resolve(nodes, answers, log=(), verified=True, config=EVERY_PAIR):
    adjudicator = ScriptedAdjudicator(answers, verified)
    return resolve_nodes(nodes, None, adjudicator, config, log=log, stamp=STAMP), adjudicator


def _people(*names):
    return tuple(node("Person", n, 100 + i) for i, n in enumerate(names))


A, B, C = _people("Ann Alpha", "Bea Beta", "Cy Gamma")


def test_a_three_name_chain_with_its_ends_distinct_ties_and_merges_nothing():
    result, _ = _resolve((A, B, C), {(A.name, B.name): Answer.SAME,
                                     (B.name, C.name): Answer.SAME})
    assert [m for m in result.proposed if m.method is Method.LLM] == []
    assert result.held_back == ()
    (cut,) = result.cut_groups
    assert cut.keys == (A.key, B.key, C.key)
    assert cut.smallest_cuts == 2  # cut A-B or B-C
    assert cut.tied == (A.key, B.key, C.key)


def test_a_related_pair_is_a_contradiction_too_and_is_listed():
    result, _ = _resolve((A, B, C), {(A.name, B.name): Answer.SAME,
                                     (B.name, C.name): Answer.SAME,
                                     (A.name, C.name): Answer.RELATED})
    assert result.merges == ()
    assert len(result.cut_groups) == 1
    assert [d.related_kind for d in result.related] == [RelatedKind.VARIANT]


def test_six_names_all_judged_same_merge_now_the_limit_is_twelve():
    six = _people("Ann Alpha", "Bea Beta", "Cy Gamma", "Di Delta", "Ed Epsilon", "Flo Zeta")
    every_same = {(x.name, y.name): Answer.SAME for x in six for y in six if x.key < y.key}
    result, _ = _resolve(six, every_same)
    (merge,) = result.merges
    assert set(merge.members) == {n.key for n in six}
    assert merge.rule is None
    assert result.held_back == ()


def test_five_names_all_judged_same_merge():
    five = _people("Ann Alpha", "Bea Beta", "Cy Gamma", "Di Delta", "Ed Epsilon")
    answers = {(x.name, y.name): Answer.SAME for x in five for y in five if x.key < y.key}
    result, _ = _resolve(five, answers)
    (merge,) = result.merges
    assert set(merge.members) == {n.key for n in five}
    assert result.held_back == ()


def test_a_clean_three_name_group_merges_with_the_most_evidenced_canonical_key():
    a, b, c = (node("Person", "Mr. Teter", 18), node("Person", "Timothy S. Teter", 16, 17),
               node("Person", "Tim Teter", 19))
    answers = {(a.name, b.name): Answer.SAME, (b.name, c.name): Answer.SAME,
               (a.name, c.name): Answer.SAME}
    result, _ = _resolve((a, b, c), answers)
    (merge,) = result.merges
    assert merge.method is Method.LLM
    assert merge.rule is None
    assert dict(merge.members) == {a.key: 1, b.key: 2, c.key: 1}
    assert merge.canonical == b.key
    assert merge.prompt_version == ADJUDICATOR_PROMPT
    assert merge.decision == "+".join(sorted(d.decision_id for d in result.decisions))
    assert {(q.key, q.chunk_id) for q in merge.evidence} == {
        (a.key, a.evidence[0].chunk_id), (b.key, b.evidence[0].chunk_id),
        (c.key, c.evidence[0].chunk_id)}
    assert merge.stamp == STAMP


def test_an_llm_canonical_key_ties_to_the_smallest_key():
    result, _ = _resolve((A, B), {(A.name, B.name): Answer.SAME})
    assert result.merges[0].canonical == A.key


def test_same_with_unverified_quotes_does_not_merge():
    result, _ = _resolve((A, B), {(A.name, B.name): Answer.SAME}, verified=False)
    assert result.merges == ()
    assert result.held_back == ()
    assert result.counts.failures == {"quote_not_found": 1}


def test_every_candidate_pair_is_adjudicated_and_none_is_left_pending():
    result, adjudicator = _resolve((A, B, C), {})
    assert len(adjudicator.judged) == 3
    assert result.pending == ()
    assert result.counts.answers == {"distinct": 3}
    assert result.counts.failures == {"distinct": 3}


def test_pairs_already_merged_deterministically_are_not_adjudicated():
    groq = (node("Organization", "Groq", 30), node("Organization", "Groq, Inc.", 31))
    other = node("Organization", "Graphcore", 32)
    result, adjudicator = _resolve((*groq, other), {}, config=ResolveConfig(top_k=0,
                                                                              fuzzy_cutoff=0.0))
    assert ("Groq", "Groq, Inc.") not in adjudicator.judged
    assert len(adjudicator.judged) == 2


def test_a_rerun_over_the_logged_merges_appends_nothing():
    first, _ = _resolve((A, B), {(A.name, B.name): Answer.SAME})
    again, _ = _resolve((A, B), {(A.name, B.name): Answer.SAME}, log=first.merges)
    assert len(first.merges) == 1
    assert again.merges == ()
    assert again.proposed == first.proposed


def test_without_an_adjudicator_candidate_pairs_stay_pending():
    result = resolve_nodes((A, B), None, None, ResolveConfig(fuzzy_cutoff=0.0, top_k=0),
                           stamp=STAMP)
    assert len(result.pending) == 1
    assert result.decisions == ()

