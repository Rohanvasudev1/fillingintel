"""The committed files of persistence scoring (Step 10, ticket 06).

Under `benchmarks/persistence/`, beside the link file and run reports:
- `frozen.json`: the persistence config hash, the adjudicator and label prompt
  versions, both models with effort, and the run whose links are scored.
  Written once, from a clean tree, before the draw.
- `scoring-draw.json`: the drawn pairs, strata and populations. Written once;
  a later run must draw the same pairs.
- `scoring-labels.jsonl`: one label per drawn pair, appended as each arrives
  (resolve.labels.PairLabel, key_a the earlier factor).
- `scoring-label-run{N}.json` per labelling run, and `scoring-score1.json`,
  the one score.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from persist.labeller import load_prompt as load_label_prompt
from persist.links import PersistLink
from persist.paths import LINKS_NAME, read_links
from persist.request import ADJUDICATOR_EFFORT, ADJUDICATOR_MODEL
from persist.sample import NEAR_RANK_FACTOR, SEED, SET_NAME, PersistDraw
from resolve.labeller import LABEL_EFFORT, LABEL_MODEL
from resolve.labels import (
    Frozen,
    LabelFileError,
    frozen_mismatches,
    model_pin,
    read_frozen,
    read_json,
    write_once,
)
from resolve.sample import SET_SIZES, DrawnPair, Stratum

FROZEN_NAME = "frozen.json"
DRAW_NAME = f"{SET_NAME}-draw.json"
LABELS_NAME = f"{SET_NAME}-labels.jsonl"
LABEL_RUN_PREFIX = f"{SET_NAME}-label-run"
SCORE_PREFIX = f"{SET_NAME}-score"


class NotFrozen(Exception):
    """The freeze file is missing, or the code, prompts or links differ from what it pins."""


@dataclass(frozen=True)
class FrozenRun:
    """The frozen settings and the persistence run whose links are scored."""

    frozen: Frozen
    report_name: str
    report: Mapping[str, object]
    links: tuple[PersistLink, ...]
    links_sha256: str


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_run(out_dir: Path, config_hash: str, persist_prompt_version: str) -> FrozenRun:
    """The frozen run, after checking the settings and links still match the freeze;
    NotFrozen when they do not, LabelFileError or ValueError on a malformed file."""
    path = out_dir / FROZEN_NAME
    frozen = read_frozen(path)
    if frozen is None:
        raise NotFrozen(f"{FROZEN_NAME} is missing; freeze with python -m persist.freeze "
                        "before the draw")
    if problems := frozen_mismatches(frozen, config_hash, current_pins(persist_prompt_version)):
        raise NotFrozen("not frozen: " + "; ".join(problems))
    provenance = read_json(path).get("provenance")
    if not isinstance(provenance, dict) or not isinstance(provenance.get("run_report"), str):
        raise LabelFileError(f"{FROZEN_NAME} does not name the frozen run report")
    report_name, links_sha = provenance["run_report"], provenance.get("links_sha256")
    report = read_json(out_dir / report_name)
    if (current := file_sha256(out_dir / LINKS_NAME)) != links_sha:
        raise NotFrozen(f"{LINKS_NAME} changed since the freeze ({current[:12]}, frozen "
                        f"{str(links_sha)[:12]})")
    return FrozenRun(frozen, report_name, report, read_links(out_dir / LINKS_NAME), current)


def current_pins(persist_prompt_version: str) -> dict[str, str]:
    """What the freeze file pins besides the config hash, as the code stands now: both
    prompts, both models with effort, and the draw's seed, near factor and sizes, which
    PersistConfig does not hold."""
    sizes = "/".join(f"{s.value}={n}" for s, n in SET_SIZES.items())
    return {"persist": persist_prompt_version,
            "label": load_label_prompt().prompt_version,
            "persist_model": model_pin(ADJUDICATOR_MODEL, ADJUDICATOR_EFFORT),
            "label_model": model_pin(LABEL_MODEL, LABEL_EFFORT),
            "draw": f"seed={SEED};near_rank_factor={NEAR_RANK_FACTOR};{sizes}"}


def draw_record(draw: PersistDraw) -> dict[str, object]:
    """The part of a draw file a rerun must reproduce exactly."""
    return {
        "set": draw.set_name, "seed": draw.seed, "config_hash": draw.config_hash,
        "sizes": {s.value: n for s, n in draw.sizes.items()},
        "populations": {s.value: n for s, n in draw.populations.items()},
        "near_rank_factor": draw.near_rank_factor,
        "pairs": [{"key_a": p.key_a, "key_b": p.key_b, "label": p.label,
                   "stratum": p.stratum.value} for p in draw.pairs],
    }


def write_draw(path: Path, draw: PersistDraw, provenance: Mapping[str, object]) -> bool:
    """Write the draw file; False if it already holds this draw. LabelFileError if another."""
    return write_once(path, draw_record(draw), provenance)


def read_draw(path: Path) -> PersistDraw:
    data = read_json(path)
    try:
        return PersistDraw(
            set_name=data["set"], seed=data["seed"], config_hash=data["config_hash"],
            populations={Stratum(s): int(n) for s, n in data["populations"].items()},
            pairs=tuple(DrawnPair(p["key_a"], p["key_b"], p["label"], Stratum(p["stratum"]))
                        for p in data["pairs"]),
            sizes={Stratum(s): int(n) for s, n in data["sizes"].items()},
            near_rank_factor=data["near_rank_factor"],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise LabelFileError(f"{path.name} is not a draw file: {exc}") from exc
