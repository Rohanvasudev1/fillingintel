"""Persistence links scored like Step 9's merges (Step 10, ticket 06).

A labelled pair (earlier key, later key) is predicted same when it is one of
the selected links, in that direction. Everything else is resolve.scoring:
weights by stratum, precision, recall, the share of true matches in the
candidate stratum, the adjudicator's accuracy on the labelled pairs it
answered, and stratified fixed-seed bootstrap intervals, under their own
namespace and seed. Every figure is agent-labelled (gpt-6-luna).
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from resolve.sample import PairKeys, Stratum
from resolve.scoring import Decisions, PairView, ScoreReport, score_predicted

SCORE_SEED = 20261015
NAMESPACE = "persist"


def score_links(labels: Sequence[PairView], links: Collection[PairKeys],
                strata: Mapping[Stratum, int], *, decisions: Decisions | None = None,
                seed: int = SCORE_SEED) -> ScoreReport:
    """Score *labels* (each pair once) against the selected *links*."""
    selected = frozenset(links)
    return score_predicted(labels, selected.__contains__, strata, decisions=decisions,
                           seed=seed, namespace=NAMESPACE)
