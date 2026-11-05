"""Sample questions: the ``dev`` questions each picker option answers from the response
cache (Step 14).

``select_samples`` asks every ``dev`` record's question through each option and
keeps the ones that answer.  ``scripts/build_ui_samples.py`` runs it with dummy
API keys, so a cache miss fails instead of spending, and writes the committed
``ui/samples.json``.  ``test`` records are never asked.  The six chips are drawn
with a fixed seed, one per class, from the default option's list.
"""
from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, get_args

from eval.schema import Class
from ingest.provenance import REPO_ROOT
from retrieve.arm import ArmError
from ui.errors import SamplesFileError
from ui.options import DEFAULT_OPTION

SAMPLES_FILE = REPO_ROOT / "ui" / "samples.json"
CHIP_SEED = 20261020  # the Step 14 grilling date
CHIP_CLASSES: tuple[str, ...] = get_args(Class)
SAMPLE_SPLIT = "dev"
_REASON_CHARS = 300


class _Record(Protocol):
    id: str
    class_: str
    question: str
    split: str


class _Runs(Protocol):
    def run(self, question: str) -> object: ...


OptionOpener = Callable[[str], AbstractContextManager[_Runs]]  # picker label -> open arm


@dataclass(frozen=True)
class Sample:
    id: str
    class_: str
    question: str


@dataclass(frozen=True)
class Skipped:
    id: str
    reason: str  # the arm's error, which names the call that missed the cache


@dataclass(frozen=True)
class Samples:
    seed: int
    options: Mapping[str, tuple[Sample, ...]]  # picker label -> replaying questions, by ID
    skipped: Mapping[str, tuple[Skipped, ...]]
    chips: tuple[Sample, ...]  # one per class, CHIP_CLASSES order


def select_samples(open_option: OptionOpener, records: Sequence[_Record],
                   options: Sequence[str], *, seed: int = CHIP_SEED) -> Samples:
    """The ``dev`` *records* each option in *options* answers; an ``ArmError`` (a cache
    miss under dummy keys) skips the question, any other error stops the build."""
    dev = sorted((r for r in records if r.split == SAMPLE_SPLIT), key=lambda r: r.id)
    found: dict[str, tuple[Sample, ...]] = {}
    skipped: dict[str, tuple[Skipped, ...]] = {}
    for label in options:
        found[label], skipped[label] = _replaying(open_option, label, dev)
    chip_pool = found.get(DEFAULT_OPTION, found[options[0]] if options else ())
    return Samples(seed, MappingProxyType(found), MappingProxyType(skipped),
                   draw_chips(chip_pool, seed=seed))


def _replaying(open_option: OptionOpener, label: str, records: Sequence[_Record],
               ) -> tuple[tuple[Sample, ...], tuple[Skipped, ...]]:
    kept, missed = [], []
    with open_option(label) as arm:
        for record in records:
            try:
                arm.run(record.question)
            except ArmError as exc:
                missed.append(Skipped(record.id, str(exc)[:_REASON_CHARS]))
                continue
            kept.append(Sample(record.id, record.class_, record.question))
    return tuple(kept), tuple(missed)


def draw_chips(pool: Sequence[Sample], *, seed: int) -> tuple[Sample, ...]:
    """One sample per class, in ``CHIP_CLASSES`` order, drawn with *seed*; a class with no
    sample gets no chip.  The draw does not depend on *pool*'s order."""
    rng = random.Random(seed)
    chips = []
    for class_ in CHIP_CLASSES:
        candidates = sorted((s for s in pool if s.class_ == class_), key=lambda s: s.id)
        if candidates:
            chips.append(rng.choice(candidates))
    return tuple(chips)


def samples_document(samples: Samples) -> dict[str, object]:
    return {
        "seed": samples.seed,
        "split": SAMPLE_SPLIT,
        "chips": [_sample(s) for s in samples.chips],
        "options": {label: [_sample(s) for s in found]
                    for label, found in samples.options.items()},
        "not_replayed": {label: [{"id": s.id, "reason": s.reason} for s in missed]
                         for label, missed in samples.skipped.items()},
    }


def _sample(sample: Sample) -> dict[str, str]:
    return {"id": sample.id, "class": sample.class_, "question": sample.question}


def write_samples(samples: Samples, path: Path = SAMPLES_FILE) -> None:
    path.write_text(json.dumps(samples_document(samples), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def read_samples(path: Path = SAMPLES_FILE) -> Samples:
    """The committed samples; ``SamplesFileError`` when the file cannot be used."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return Samples(
            seed=int(document["seed"]),
            options=MappingProxyType({label: tuple(_parse(s) for s in found)
                                      for label, found in document["options"].items()}),
            skipped=MappingProxyType({label: tuple(Skipped(str(s["id"]), str(s["reason"]))
                                                   for s in missed)
                                      for label, missed in document["not_replayed"].items()}),
            chips=tuple(_parse(s) for s in document["chips"]),
        )
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise SamplesFileError(f"cannot read {path.name}: {type(exc).__name__}: {exc}") from exc


def _parse(entry: Mapping[str, object]) -> Sample:
    return Sample(str(entry["id"]), str(entry["class"]), str(entry["question"]))
