"""Group cut: a held-back group is cut into pieces instead of dropped (Step 9b).

The cut removes the fewest `same` answers so that no `related` or `distinct`
pair shares a piece; deterministic merges are never cut; keys that tied
smallest cuts place differently stay unmerged; each piece within the name
limit (12) merges as an `llm` merge with rule `group_cut`. The adjudicator is
the scripted fake, answering `unsure` to every pair it is not given, so only
the scripted answers matter.
"""
import random

from resolve.config import ResolveConfig
from resolve.decisions import Answer, PairDecision
from resolve.groups import GROUP_CUT_RULE, group_decisions
from resolve.log import Method, Stamp, replay
from resolve.pipeline import resolve_nodes
from tests.resolve_helpers import ScriptedAdjudicator, node

STAMP = Stamp(config_hash="c" * 64, commit="abc1234", timestamp="2026-10-12T12:00:00+00:00")
EVERY_PAIR = ResolveConfig(fuzzy_cutoff=0.0, top_k=0)
SAME, RELATED, DISTINCT, UNSURE = Answer.SAME, Answer.RELATED, Answer.DISTINCT, Answer.UNSURE


def _resolve(nodes, answers, log=()):
    adjudicator = ScriptedAdjudicator(answers, default=UNSURE)
    return resolve_nodes(nodes, None, adjudicator, EVERY_PAIR, log=log, stamp=STAMP)


def _products(*names):
    return tuple(node("Product", n, 200 + i, key=f"Product:{i:02d} {n.casefold()}")
                 for i, n in enumerate(names))


def _clique(nodes, answer=SAME):
    return {(x.name, y.name): answer for i, x in enumerate(nodes) for y in nodes[i + 1:]}


def _llm(result):
    return [m for m in result.proposed if m.method is Method.LLM]


def _pieces(result):
    return sorted(sorted(m.members) for m in _llm(result))


# The EPYC brand chained to one product line by a single `same` answer, which
# the adjudicator kept apart from the brand twice.
EPYC, AMD_EPYC, SERIES_9005, GEN5, AMD_9005 = _products(
    "EPYC", "AMD EPYC", "EPYC 9005 Series processors", "5th Gen AMD EPYC processors",
    "AMD EPYC 9005")
BRAND, LINE = (EPYC, AMD_EPYC), (SERIES_9005, GEN5, AMD_9005)
EPYC_ANSWERS = {**_clique(BRAND), **_clique(LINE), (AMD_EPYC.name, AMD_9005.name): SAME,
                (EPYC.name, GEN5.name): RELATED, (AMD_EPYC.name, SERIES_9005.name): RELATED}


def test_a_brand_chained_to_its_product_line_is_cut_into_two_pieces():
    result = _resolve((*BRAND, *LINE), EPYC_ANSWERS)
    assert _pieces(result) == sorted([sorted(n.key for n in BRAND),
                                      sorted(n.key for n in LINE)])
    assert result.held_back == ()
    (cut,) = result.cut_groups
    assert set(cut.keys) == {n.key for n in (*BRAND, *LINE)}
    assert [(d.key_a, d.key_b) for d in cut.cut] == [
        tuple(sorted((AMD_EPYC.key, AMD_9005.key)))]
    assert cut.tied == ()


def test_a_piece_is_an_llm_merge_with_rule_group_cut_citing_only_its_kept_answers():
    result = _resolve((*BRAND, *LINE), EPYC_ANSWERS)
    line = next(m for m in _llm(result) if SERIES_9005.key in m.members)
    assert line.method is Method.LLM
    assert line.rule == GROUP_CUT_RULE
    assert line.decision is None
    assert {q.key for q in line.evidence} == {n.key for n in LINE}
    assert len(line.evidence) == 2 * 3  # three kept `same` answers, two quotes each
    assert line.prompt_version == ScriptedAdjudicator.prompt_version


def test_a_rerun_over_the_logged_pieces_appends_nothing():
    first = _resolve((*BRAND, *LINE), EPYC_ANSWERS)
    again = _resolve((*BRAND, *LINE), EPYC_ANSWERS, log=first.merges)
    assert len(first.merges) == 2
    assert again.merges == ()
    assert again.proposed == first.proposed


def test_a_group_of_eight_names_with_no_contradiction_merges_as_one():
    eight = _products(*(f"Instinct MI3{i}0X" for i in range(8)))
    chain = {(x.name, y.name): SAME for x, y in zip(eight, eight[1:], strict=False)}
    result = _resolve(eight, chain)
    (merge,) = _llm(result)
    assert set(merge.members) == {n.key for n in eight}
    assert merge.rule is None  # an ordinary merge, as in Step 9
    assert result.held_back == ()
    assert result.cut_groups == ()


def test_a_piece_over_twelve_names_is_held_back_with_its_reason():
    thirteen = _products(*(f"Radeon RX {7000 + 100 * i}" for i in range(13)))
    chain = {(x.name, y.name): SAME for x, y in zip(thirteen, thirteen[1:], strict=False)}
    result = _resolve(thirteen, chain)
    assert _llm(result) == []
    (held,) = result.held_back
    assert len(held.keys) == 13
    assert held.reason == "piece has 13 names, more than 12"


def test_a_cut_piece_over_twelve_names_is_held_back_with_its_reason():
    # 11 processors all judged the same, two of them printed twice (deterministic
    # merges): 13 names in 11 parts. "Ryzen" is joined to one and related to another.
    big = _products(*(f"Ryzen {i} processor" for i in range(11)))
    twins = tuple(node("Product", f"Ryzen {i} Processor", 250 + i, key=f"Product:twin {i}")
                  for i in (3, 4))
    ryzen = node("Product", "Ryzen", 300, key="Product:ryzen")
    answers = {**_clique(big), (ryzen.name, big[0].name): SAME,
               (ryzen.name, big[1].name): RELATED}
    result = _resolve((*big, *twins, ryzen), answers)
    assert _llm(result) == []
    (held,) = result.held_back
    assert set(held.keys) == {n.key for n in (*big, *twins)}
    assert held.reason == "piece has 13 names, more than 12"
    (cut,) = result.cut_groups
    assert [(d.key_a, d.key_b) for d in cut.cut] == [tuple(sorted((ryzen.key, big[0].key)))]


# Two pairs that always merge (A, B) and (E, F), and D between them by one
# `same` answer each way; every pair across (A, B) and (E, F) is `distinct`.
A, B, D, E, F = (node("Organization", n, 400 + i) for i, n in enumerate(
    ("Alpha Corp", "Alpha Holdings", "Delta Group", "Echo Ltd", "Echo Systems")))
TIED = {(A.name, B.name): SAME, (E.name, F.name): SAME, (D.name, B.name): SAME,
        (D.name, E.name): SAME, **{(x.name, y.name): DISTINCT for x in (A, B) for y in (E, F)}}


def test_tied_cuts_leave_the_disputed_key_unmerged_and_merge_the_rest():
    result = _resolve((A, B, D, E, F), TIED)
    assert _pieces(result) == sorted([sorted([A.key, B.key]), sorted([E.key, F.key])])
    (cut,) = result.cut_groups
    assert cut.tied == (D.key,)
    assert D.key not in replay(result.proposed)


def test_unsure_answers_never_cause_a_cut():
    a, b, c = _products("Versal", "AMD Versal", "Versal adaptive SoCs")
    result = _resolve((a, b, c), {(a.name, b.name): SAME, (b.name, c.name): SAME})
    (merge,) = _llm(result)
    assert set(merge.members) == {a.key, b.key, c.key}
    assert result.cut_groups == ()


def test_a_contradiction_through_a_deterministic_group_cuts_the_llm_answer():
    groq, groq_inc = node("Organization", "Groq", 30), node("Organization", "Groq, Inc.", 31)
    cloud = node("Organization", "GroqCloud", 32)
    result = _resolve((groq, groq_inc, cloud), {(groq.name, cloud.name): SAME,
                                                (groq_inc.name, cloud.name): DISTINCT})
    assert [m.method for m in result.proposed] == [Method.DETERMINISTIC]
    assert result.held_back == ()
    (cut,) = result.cut_groups
    assert [d.answer for d in cut.cut] == [SAME]
    assert replay(result.proposed) == {groq.key: groq.key, groq_inc.key: groq.key}


def test_a_piece_holding_a_filer_maps_to_its_company_key():
    amd, advanced = node("Organization", "AMD", 500), node("Organization", "Advanced Micro", 501)
    amd_xilinx, xilinx = node("Organization", "AMD Xilinx", 502), node("Organization", "Xilinx",
                                                                      503, 504)
    answers = {(amd.name, advanced.name): SAME, (advanced.name, amd_xilinx.name): SAME,
               (amd_xilinx.name, xilinx.name): SAME, (amd.name, xilinx.name): RELATED,
               (advanced.name, xilinx.name): RELATED, (amd.name, amd_xilinx.name): RELATED}
    result = _resolve((amd, advanced, amd_xilinx, xilinx), answers)
    aliases = replay(result.proposed)
    assert aliases[advanced.key] == aliases[amd.key] == "Company:2488"
    assert aliases[amd_xilinx.key] == aliases[xilinx.key] == xilinx.key  # most evidence


def test_the_canonical_key_of_a_piece_is_the_most_evidenced_member():
    long_line = node("Product", "AMD EPYC 9005", 210, 211, 212, key=AMD_9005.key)
    result = _resolve((*BRAND, SERIES_9005, GEN5, long_line), EPYC_ANSWERS)
    line = next(m for m in _llm(result) if SERIES_9005.key in m.members)
    assert line.canonical == long_line.key


# ── group_decisions() directly ────────────────────────────────────────────────

def _decision(key_a, key_b, answer, label="Organization"):
    key_a, key_b = sorted((key_a, key_b))
    return PairDecision(
        decision_id=f"d-{key_a}-{key_b}", key_a=key_a, key_b=key_b, label=label,
        answer=answer, related_kind=None, quote_a="q", chunk_id_a="c", quote_b="q",
        chunk_id_b="c", reason="hand-built", quotes_verified=True,
        failure=None if answer is SAME else answer.value, prompt_version="resolve/v2@x")


def test_a_contradiction_inside_a_deterministic_merge_stays_held_back():
    decisions = [_decision("Organization:a", "Organization:c", SAME),
                 _decision("Organization:a", "Organization:b", DISTINCT)]
    grouping = group_decisions(decisions, joined=[("Organization:a", "Organization:b")])
    assert grouping.clean == ()
    (held,) = grouping.held_back
    assert held.reason == "no cut separates the contradiction"


def test_the_result_does_not_depend_on_the_order_of_the_decisions():
    keys = [f"Product:{name}" for name in "abcdefgh"]
    rng = random.Random(7)
    decisions = [_decision(x, y, SAME, "Product") for i, x in enumerate(keys)
                 for y in keys[i + 1:] if rng.random() < 0.5]
    decisions += [_decision(keys[0], keys[7], DISTINCT, "Product"),
                  _decision(keys[1], keys[6], RELATED, "Product"),
                  _decision(keys[2], keys[5], DISTINCT, "Product")]
    expected = group_decisions(decisions)
    for seed in range(5):
        shuffled = list(decisions)
        random.Random(seed).shuffle(shuffled)
        assert group_decisions(shuffled) == expected


def test_a_group_whose_units_exceed_the_search_bound_stays_held_back():
    keys = [f"Product:{i:02d}" for i in range(14)]
    decisions = [_decision(x, y, SAME, "Product") for x, y in zip(keys, keys[1:], strict=False)]
    decisions.append(_decision(keys[0], keys[13], DISTINCT, "Product"))
    grouping = group_decisions(decisions)
    assert grouping.clean == ()
    (held,) = grouping.held_back
    assert held.reason == ("14 names after deterministic merges, more than the "
                           "search bound of 12")


STEP_9_FROZEN_HASH = "7a5aa8de76e57b0a39f9120305b2beaf583f081e07deca2dd9c83ee1a3741205"


def test_the_name_limit_and_the_group_rule_are_in_the_config_hash():
    base = ResolveConfig()
    assert base.max_group_names == 12
    assert base.to_dict()["group_rule"] == "group_cut/v1"
    assert ResolveConfig(max_group_names=5).config_hash != base.config_hash
    assert base.config_hash != STEP_9_FROZEN_HASH  # the same candidate rules as frozen.json


# ── The run report's section on cut groups ────────────────────────────────────

def test_the_report_lists_each_cut_group_and_counts_them():
    from resolve.report import build_group_cuts

    result = _resolve((*BRAND, *LINE, A, B, D, E, F), {**EPYC_ANSWERS, **TIED})
    section = build_group_cuts(result)
    assert section["counts"] == {"cut_groups": 2, "pieces_merged": 4, "keys_left_by_ties": 1,
                                 "held_back": 0}
    epyc = next(g for g in section["groups"] if EPYC.key in g["keys"])
    assert epyc["pieces"] == sorted([sorted(n.key for n in BRAND), sorted(n.key for n in LINE)])
    assert [(c["key_a"], c["key_b"], c["answer"]) for c in epyc["cut"]] == [
        (*sorted((AMD_EPYC.key, AMD_9005.key)), "same")]
    assert epyc["tied"] == [] and epyc["smallest_cuts"] == 1
    tied = next(g for g in section["groups"] if D.key in g["keys"])
    assert tied["tied"] == [D.key] and tied["smallest_cuts"] == 2


def test_a_piece_is_undone_by_an_ordinary_split():
    from resolve.log import split_event

    result = _resolve((*BRAND, *LINE), EPYC_ANSWERS)
    line = next(m for m in _llm(result) if SERIES_9005.key in m.members)
    undone = replay([*result.proposed, split_event(line.merge_id, Method.USER, "test", STAMP)])
    assert not {n.key for n in LINE} & set(undone)
    assert undone[AMD_EPYC.key] == undone[EPYC.key]
