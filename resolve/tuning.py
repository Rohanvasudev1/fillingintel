"""Tuning numbers on the labelled tuning set (Step 9, ticket 06).

For a config, `rule_check` says how many of the tuning set's true matches the
candidate rules find and how many non-matches they flag; `adjudicator_check`
says how often the adjudicator merged exactly the pairs labelled same. The
labels come from gpt-6-luna, so every number is "agent-labelled (gpt-6-luna)".
`python -m resolve.tune` runs both and writes the tuning report.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace

from resolve.candidates import ScoredPair
from resolve.config import FUZZY_OFF, ResolveConfig

PairKeys = tuple[str, str]

_START = ResolveConfig(fuzzy_cutoff=90.0, top_k=3, rare_word_max_keys=3)  # ticket 02's values
CHOSEN = "chosen"  # the name of the config chosen on the tuning set
# The configs compared on the tuning set; CHOSEN names ResolveConfig's defaults.
CONFIGS: Mapping[str, ResolveConfig] = {
    "start": _START,
    "embedding_off": replace(_START, top_k=0),
    "top_k_1": replace(_START, top_k=1),
    "top_k_2": replace(_START, top_k=2),
    "rare_word_off": replace(_START, rare_word_max_keys=0),
    "fuzzy_95": replace(_START, fuzzy_cutoff=95.0),
    "fuzzy_85": replace(_START, fuzzy_cutoff=85.0),
    "fuzzy_off": replace(_START, fuzzy_cutoff=FUZZY_OFF),
    "name_span": replace(_START, embed_text="name_span"),
    "name_span_top_k_1": replace(_START, embed_text="name_span", top_k=1),
    "fuzzy_85_rare_word_off_name_span": replace(_START, fuzzy_cutoff=85.0,
                                                rare_word_max_keys=0, embed_text="name_span"),
    CHOSEN: ResolveConfig(),
}


@dataclass(frozen=True, slots=True)
class TunedLabel:
    """A tuning label as tuning reads it: the pair and the labeller's verdict."""

    key_a: str
    key_b: str
    same: bool

    @property
    def keys(self) -> PairKeys:
        return (self.key_a, self.key_b)


@dataclass(frozen=True, slots=True)
class RuleCheck:
    pending: int  # pairs the config would send to the adjudicator, over the whole corpus
    matches_found: int  # labelled matches some rule made a candidate
    matches: int
    non_matches_flagged: int
    non_matches: int
    missed: tuple[PairKeys, ...]  # labelled matches no rule found


@dataclass(frozen=True, slots=True)
class AdjudicatorCheck:
    answered: int
    agreed: int  # merged exactly when the label says same
    merged_but_not_same: tuple[PairKeys, ...]
    same_but_not_merged: tuple[PairKeys, ...]


def _either(keys: PairKeys) -> tuple[PairKeys, PairKeys]:
    return keys, (keys[1], keys[0])


def rule_check(pairs: Sequence[ScoredPair], pending: int,
               labels: Sequence[TunedLabel]) -> RuleCheck:
    """How the rules that scored *pairs* do on the labelled pairs."""
    candidate: dict[PairKeys, bool] = {}
    for p in pairs:
        for keys in _either((p.key_a, p.key_b)):
            candidate[keys] = p.is_candidate
    matches = [lb for lb in labels if lb.same]
    others = [lb for lb in labels if not lb.same]
    missed = tuple(lb.keys for lb in matches if not candidate.get(lb.keys, False))
    return RuleCheck(pending=pending, matches_found=len(matches) - len(missed),
                     matches=len(matches),
                     non_matches_flagged=sum(candidate.get(lb.keys, False) for lb in others),
                     non_matches=len(others), missed=missed)


def adjudicator_check(merged: Mapping[PairKeys, bool],
                      labels: Sequence[TunedLabel]) -> AdjudicatorCheck:
    """Agreement between the adjudicator's merges and the labels, on the pairs it judged."""
    looked_up = ((lb, merged.get(lb.keys, merged.get(lb.keys[::-1]))) for lb in labels)
    judged = [(lb, m) for lb, m in looked_up if m is not None]
    return AdjudicatorCheck(
        answered=len(judged), agreed=sum(m == lb.same for lb, m in judged),
        merged_but_not_same=tuple(lb.keys for lb, m in judged if m and not lb.same),
        same_but_not_merged=tuple(lb.keys for lb, m in judged if lb.same and not m))


def check_to_dict(check: RuleCheck | AdjudicatorCheck) -> dict[str, object]:
    return {k: ([list(keys) for keys in v] if isinstance(v, tuple) else v)
            for k, v in asdict(check).items()}
