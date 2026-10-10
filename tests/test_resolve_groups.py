"""resolve_nodes() with an adjudicator: groups of `same` decisions, held back or merged (Step 9).

The adjudicator is a fake that answers from a script, so these tests pin the
grouping rules alone: union-find over merging decisions, a contradiction or more
than 5 names holds a group back, the canonical key, and reruns. The real
adjudicator's reply checks are in test_resolve_adjudicate.py.
"""
from resolve.config import ResolveConfig
from resolve.decisions import Answer, PairDecision, RelatedKind
from resolve.log import Method, Stamp
from resolve.pipeline import resolve_nodes
from tests.resolve_helpers import node

STAMP = Stamp(config_hash="c" * 64, commit="abc1234", timestamp="2026-10-10T12:00:00+00:00")
EVERY_PAIR = ResolveConfig(fuzzy_cutoff=0.0, top_k=0)  # every same-label pair is a candidate
PROMPT = "resolve/v1@0123abcd"


class ScriptedAdjudicator:
    """Answers each pair from *answers* (keyed by the two names), `distinct` otherwise."""

    prompt_version = PROMPT

    def __init__(self, answers, verified=True):
        self._answers = {frozenset(k): v for k, v in answers.items()}
        self._verified = verified
        self.judged = []

    def judge(self, pair, a, b):
        self.judged.append((a.name, b.name))
        answer = self._answers.get(frozenset((a.name, b.name)), Answer.DISTINCT)
        kind = RelatedKind.VARIANT if answer is Answer.RELATED else None
        failure = None if answer is Answer.SAME else (
            f"related:{kind}" if kind else answer.value)
        if answer is Answer.SAME and not self._verified:
            failure = "quote_not_found"
        return PairDecision(
            decision_id=f"d-{a.name}-{b.name}", key_a=pair.key_a, key_b=pair.key_b,
            label=pair.label, answer=answer, related_kind=kind,
            quote_a=a.evidence[0].span, chunk_id_a=a.evidence[0].chunk_id,
            quote_b=b.evidence[0].span, chunk_id_b=b.evidence[0].chunk_id,
            reason="scripted", quotes_verified=self._verified, failure=failure,
            prompt_version=PROMPT)


def _resolve(nodes, answers, log=(), verified=True, config=EVERY_PAIR):
    adjudicator = ScriptedAdjudicator(answers, verified)
    return resolve_nodes(nodes, None, adjudicator, config, log=log, stamp=STAMP), adjudicator


def _people(*names):
    return tuple(node("Person", n, 100 + i) for i, n in enumerate(names))


A, B, C = _people("Ann Alpha", "Bea Beta", "Cy Gamma")


def test_a_contradiction_holds_the_whole_group_back():
    result, _ = _resolve((A, B, C), {(A.name, B.name): Answer.SAME,
                                     (B.name, C.name): Answer.SAME})
    assert [m for m in result.merges if m.method is Method.LLM] == []
    (held,) = result.held_back
    assert held.keys == (A.key, B.key, C.key)
    assert "contradiction" in held.reason
    assert {(d.key_a, d.key_b) for d in held.decisions} == {
        (A.key, B.key), (B.key, C.key), (A.key, C.key)}


def test_a_related_pair_inside_a_group_is_a_contradiction_too():
    result, _ = _resolve((A, B, C), {(A.name, B.name): Answer.SAME,
                                     (B.name, C.name): Answer.SAME,
                                     (A.name, C.name): Answer.RELATED})
    assert result.merges == ()
    assert len(result.held_back) == 1
    assert [d.related_kind for d in result.related] == [RelatedKind.VARIANT]


def test_a_six_name_chain_is_held_back():
    six = _people("Ann Alpha", "Bea Beta", "Cy Gamma", "Di Delta", "Ed Epsilon", "Flo Zeta")
    chain = {(x.name, y.name): Answer.SAME for x, y in zip(six, six[1:], strict=False)}
    every_same = {(x.name, y.name): Answer.SAME for x in six for y in six if x.key < y.key}
    for answers in (chain, every_same):
        result, _ = _resolve(six, answers)
        assert result.merges == ()
        (held,) = result.held_back
        assert len(held.keys) == 6
        assert "6 names" in held.reason


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
    assert merge.prompt_version == PROMPT
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


def test_a_contradiction_through_a_deterministic_group_holds_the_llm_group_back():
    groq, groq_inc = node("Organization", "Groq", 30), node("Organization", "Groq, Inc.", 31)
    other = node("Organization", "GroqCloud", 32)
    result, _ = _resolve((groq, groq_inc, other), {(groq.name, other.name): Answer.SAME,
                                                   (groq_inc.name, other.name): Answer.DISTINCT})
    assert [m.method for m in result.merges] == [Method.DETERMINISTIC]
    (held,) = result.held_back
    assert set(held.keys) == {groq.key, groq_inc.key, other.key}


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

