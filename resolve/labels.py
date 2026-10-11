"""The labelled-pair record and the committed draw and label files (Step 9, ticket 04).

Under `benchmarks/resolution/`, per set (`tuning` or `scoring`):
- `{set}-draw.json`: the drawn pairs, their strata, the populations and the
  draw's provenance. Written once; a later run must draw the same pairs, or
  it stops.
- `{set}-labels.jsonl`: one labelled pair per line, appended as each label
  arrives, so a stopped run resumes where it left off. A pair is never
  labelled twice.

`frozen.json` holds the config hash and prompt versions that ticket 06 freezes
after tuning; the scoring draw refuses to run without it.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from ingest.atomic_json import write_json_atomic
from resolve.sample import SETS, Draw, DrawnPair, PairKeys, Stratum

FROZEN_NAME = "frozen.json"
TUNING_REPORT_NAME = "tuning-report.json"
_DRAW_FIELDS = ("set", "seed", "config_hash", "sizes", "populations", "near_fuzzy_margin",
                "near_rank_factor", "pairs")


class LabelFileError(ValueError):
    """A draw, label or frozen file is malformed, or a write would change what is there."""


def draw_path(directory: Path, set_name: str) -> Path:
    return directory / f"{set_name}-draw.json"


def labels_path(directory: Path, set_name: str) -> Path:
    return directory / f"{set_name}-labels.jsonl"


@dataclass(frozen=True, slots=True)
class PairLabel:
    """One labeller verdict on a drawn pair (key_a < key_b)."""

    set_name: str
    key_a: str
    key_b: str
    label: str
    stratum: Stratum
    same: bool
    quote_a: str
    chunk_id_a: str
    quote_b: str
    chunk_id_b: str
    quotes_verified: bool  # each quote found in a shown chunk of its own node
    reason: str
    model: str
    effort: str
    prompt_version: str

    @property
    def keys(self) -> PairKeys:
        return (self.key_a, self.key_b)

    def to_line(self) -> dict[str, object]:
        return {**asdict(self), "stratum": self.stratum.value}

    @classmethod
    def from_line(cls, line: Mapping[str, object]) -> PairLabel:
        fields = cls.__dataclass_fields__
        if set(line) != set(fields):
            raise LabelFileError(f"a label line has fields {sorted(line)}")
        values = {**line, "stratum": Stratum(line["stratum"])}
        for name, value in values.items():
            expected = bool if name in ("same", "quotes_verified") else (Stratum, str)
            if not isinstance(value, expected):
                raise LabelFileError(f"a label line's {name} has the wrong type")
        return cls(**values)


def draw_record(draw: Draw) -> dict[str, object]:
    """The part of a draw file a rerun must reproduce exactly."""
    return {
        "set": draw.set_name, "seed": draw.seed, "config_hash": draw.config_hash,
        "sizes": {s.value: n for s, n in draw.sizes.items()},
        "populations": {s.value: n for s, n in draw.populations.items()},
        "near_fuzzy_margin": draw.near_fuzzy_margin, "near_rank_factor": draw.near_rank_factor,
        "pairs": [{"key_a": p.key_a, "key_b": p.key_b, "label": p.label,
                   "stratum": p.stratum.value} for p in draw.pairs],
    }


def write_draw(path: Path, draw: Draw, provenance: Mapping[str, object]) -> bool:
    """Write the draw file; False if it already holds this draw. LabelFileError if another."""
    record = draw_record(draw)
    if path.exists():
        kept = read_json(path)
        if {k: kept.get(k) for k in _DRAW_FIELDS} != record:
            raise LabelFileError(f"{path.name} holds another draw; the pairs, strata or config "
                                 "changed since it was written")
        return False
    write_json_atomic(path, {**record, "provenance": dict(provenance)}, indent=2)
    return True


def read_draw(path: Path) -> Draw:
    data = read_json(path)
    try:
        return Draw(
            set_name=data["set"], seed=data["seed"], config_hash=data["config_hash"],
            populations={Stratum(s): int(n) for s, n in data["populations"].items()},
            pairs=tuple(DrawnPair(p["key_a"], p["key_b"], p["label"], Stratum(p["stratum"]))
                        for p in data["pairs"]),
            sizes={Stratum(s): int(n) for s, n in data["sizes"].items()},
            near_fuzzy_margin=data["near_fuzzy_margin"],
            near_rank_factor=data["near_rank_factor"],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise LabelFileError(f"{path.name} is not a draw file: {exc}") from exc


def read_labels(path: Path) -> tuple[PairLabel, ...]:
    """The labels in *path*, in file order; an absent file has none."""
    if not path.exists():
        return ()
    labels = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            line = json.loads(raw)
            if not isinstance(line, dict):
                raise LabelFileError("not an object")
            labels.append(PairLabel.from_line(line))
        except ValueError as exc:  # LabelFileError, JSON and Stratum errors
            raise LabelFileError(f"{path.name} line {number}: {exc}") from exc
    seen: set[PairKeys] = set()
    for label in labels:
        if label.keys in seen:
            raise LabelFileError(f"{path.name} labels {label.key_a} / {label.key_b} twice")
        seen.add(label.keys)
    return tuple(labels)


def append_labels(path: Path, labels: Iterable[PairLabel]) -> None:
    """Append *labels*; LabelFileError, writing nothing, if any pair is already labelled."""
    new = list(labels)
    kept = {label.keys for label in read_labels(path)}
    again = [label for label in new if label.keys in kept]
    if again or len({label.keys for label in new}) != len(new):
        first = (again or new)[0]
        raise LabelFileError(f"{first.key_a} / {first.key_b} is already labelled; "
                             "labels are written once")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for label in new:
            fh.write(json.dumps(label.to_line(), ensure_ascii=False) + "\n")


@dataclass(frozen=True, slots=True)
class Frozen:
    """What ticket 06 froze before the scoring draw."""

    config_hash: str
    prompts: Mapping[str, str]  # "label" and "resolve" to their prompt versions


def read_frozen(path: Path) -> Frozen | None:
    """The frozen settings in *path*, or None when nothing is frozen yet."""
    if not path.exists():
        return None
    data = read_json(path)
    prompts = data.get("prompts")
    if not isinstance(data.get("config_hash"), str) or not isinstance(prompts, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in prompts.items()):
        raise LabelFileError(f"{path.name} needs a config_hash and a prompts object")
    return Frozen(data["config_hash"], dict(prompts))


def write_frozen(path: Path, frozen: Frozen, provenance: Mapping[str, object]) -> None:
    """Freeze once; LabelFileError if *path* exists."""
    if path.exists():
        raise LabelFileError(f"{path.name} exists; settings are frozen once")
    write_json_atomic(path, {"config_hash": frozen.config_hash, "prompts": dict(frozen.prompts),
                             "provenance": dict(provenance)}, indent=2)


def frozen_mismatches(frozen: Frozen, config_hash: str,
                      prompts: Mapping[str, str]) -> Sequence[str]:
    """What differs between the frozen settings and the current ones, as readable lines."""
    found = [f"config hash {config_hash[:12]} is not the frozen {frozen.config_hash[:12]}"] \
        if config_hash != frozen.config_hash else []
    for name in sorted(set(frozen.prompts) | set(prompts)):
        if frozen.prompts.get(name) != prompts.get(name):
            found.append(f"{name} prompt {prompts.get(name)} is not the frozen "
                         f"{frozen.prompts.get(name)}")
    return found


def label_status(directory: Path) -> dict[str, object]:
    """Per set, how many pairs are drawn and labelled so far (for the run report)."""
    status: dict[str, object] = {}
    for set_name in SETS:
        path = draw_path(directory, set_name)
        drawn = read_draw(path) if path.exists() else None
        labels = read_labels(labels_path(directory, set_name))
        status[set_name] = {"drawn": None if drawn is None else len(drawn.pairs),
                            "labelled": len(labels),
                            "same": sum(label.same for label in labels)}
    return status


def tuning_status(directory: Path) -> dict[str, object] | None:
    """The tuning choices and frozen settings (ticket 06), for the run report; None before."""
    path = directory / TUNING_REPORT_NAME
    if not path.exists():
        return None
    report = read_json(path)
    rules = report.get("rules")
    chosen = next((r for r in rules if isinstance(r, dict) and r.get("name") == "chosen"),
                  None) if isinstance(rules, list) else None
    adjudicator = report.get("adjudicator")
    frozen = read_frozen(directory / FROZEN_NAME)
    return {
        "report": path.name, "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "chosen": chosen,
        "adjudicator": {k: adjudicator.get(k) for k in ("prompt_version", "answered", "agreed")}
        if isinstance(adjudicator, dict) else None,
        "frozen": None if frozen is None else {"config_hash": frozen.config_hash,
                                               "prompts": dict(frozen.prompts)},
    }


def read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise LabelFileError(f"{path.name} is not JSON") from exc
    if not isinstance(data, dict):
        raise LabelFileError(f"{path.name} is not an object")
    return data
