"""resolve_nodes() over real voyage-4-large vectors recorded from NVDA run 1 (Step 9).

The vectors come from tests/fixtures/voyage/entity_vectors.json.gz. These tests
pin what the embedding rule finds on real names, which the lexical rules cannot.
"""
import gzip
import json
from pathlib import Path

import pytest

from resolve.candidates import Rule
from resolve.config import ResolveConfig
from resolve.log import Stamp
from resolve.pipeline import resolve_nodes
from tests.resolve_helpers import node

FIXTURE = Path(__file__).parent / "fixtures" / "voyage" / "entity_vectors.json.gz"
STAMP = Stamp(config_hash="c" * 64, commit="abc1234", timestamp="2026-10-10T12:00:00+00:00")


class RecordedEmbedder:
    def __init__(self):
        with gzip.open(FIXTURE, "rt", encoding="utf-8") as fh:
            self._vectors = json.load(fh)["vectors"]

    def embed(self, texts):
        return [tuple(self._vectors[t]) for t in texts]


def _pairs(nodes, top_k=1):
    result = resolve_nodes(nodes, RecordedEmbedder(), None, ResolveConfig(top_k=top_k),
                           stamp=STAMP)
    return {frozenset((p.key_a, p.key_b)): p for p in result.pending}, result


def test_the_recorded_vectors_are_unit_length_voyage_vectors():
    for vector in RecordedEmbedder()._vectors.values():
        assert len(vector) == 1024
        assert sum(x * x for x in vector) == pytest.approx(1.0, abs=1e-3)


def test_mr_teter_is_the_nearest_person_to_timothy_s_teter_by_embedding_alone():
    teter, full, kress = (node("Person", "Mr. Teter"), node("Person", "Timothy S. Teter"),
                          node("Person", "Colette M. Kress"))
    pending, _ = _pairs((teter, full, kress))
    pair = pending[frozenset((teter.key, full.key))]
    assert Rule.EMBEDDING in pair.rules and pair.embedding_rank == 1
    assert Rule.FUZZY not in pair.rules


def test_gdpr_meets_both_its_long_name_and_the_uk_regime():
    gdpr, full, uk = (node("Regulation", "GDPR"),
                      node("Regulation", "General Data Protection Regulation"),
                      node("Regulation", "U.K. GDPR"))
    pending, _ = _pairs((gdpr, full, uk))
    assert {Rule.ACRONYM, Rule.EMBEDDING} <= pending[frozenset((gdpr.key, full.key))].rules
    assert Rule.FUZZY in pending[frozenset((gdpr.key, uk.key))].rules


def test_groq_merges_deterministically_and_huawei_waits_for_the_adjudicator():
    nodes = (node("Organization", "Groq", 30), node("Organization", "Groq, Inc", 31),
             node("Organization", "Groq, Inc.", 32), node("Organization", "Huawei", 33),
             node("Organization", "Huawei Technologies Co. Ltd.", 34))
    pending, result = _pairs(nodes, top_k=2)
    (merge,) = result.merges
    assert set(merge.members) == {"Organization:groq", "Organization:groq, inc",
                                  "Organization:groq, inc."}
    huawei = pending[frozenset(("Organization:huawei",
                                "Organization:huawei technologies co. ltd."))]
    assert {Rule.FUZZY, Rule.EMBEDDING} <= huawei.rules
