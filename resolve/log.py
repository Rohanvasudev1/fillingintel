"""The merge log and replay() (Step 9, ADR-0006).

The log is append-only JSONL. A `merge` event joins a group of keys of one
label (or an Organization into its filer's Company); a `split` event reverses
one earlier merge by its ID. Nothing is ever edited or removed, so every merge
keeps its deciding evidence and can be undone.

A merge's ID is a hash of its members, method and rule or decision, so the same
merge proposed again has the same ID; new_merges() drops any ID already logged,
split or not, which makes a rerun on unchanged inputs append nothing and keeps
a split merge from coming back.

replay() rebuilds the alias map: the active merges (logged, not split) are
joined by shared keys, and each group's members map to the canonical key that
groups.canonical_key() picks from the chunk counts the events recorded (the
latest event's count for a key). Keys in no active merge are their own
canonical key and are left out of the map.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from resolve.groups import canonical_key, connected

MERGE, SPLIT = "merge", "split"


class Method(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    USER = "user"


class LogError(ValueError):
    """The merge log is malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class Stamp:
    """Who wrote an event: the run's config hash, the commit and the time."""

    config_hash: str
    commit: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class EvidenceQuote:
    """The evidence a merge rests on for one member: its name and a quote from its chunk."""

    key: str
    name: str
    chunk_id: str | None  # None for the filer table's own entry
    quote: str


@dataclass(frozen=True)
class MergeEvent:
    merge_id: str
    label: str
    members: Mapping[str, int]  # key to its evidence chunk count when merged
    canonical: str
    method: Method
    rule: str | None
    decision: str | None
    evidence: tuple[EvidenceQuote, ...]
    prompt_version: str | None
    stamp: Stamp


@dataclass(frozen=True)
class SplitEvent:
    merge_id: str
    method: Method
    reason: str
    stamp: Stamp


LogEvent = MergeEvent | SplitEvent


def _merge_id(members: Iterable[str], method: Method, basis: str) -> str:
    text = json.dumps([sorted(members), method.value, basis], separators=(",", ":"))
    return "m-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def merge_event(*, label: str, members: Mapping[str, int], method: Method, rule: str | None = None,
                decision: str | None = None, evidence: Sequence[EvidenceQuote] = (),
                prompt_version: str | None = None, stamp: Stamp) -> MergeEvent:
    """A merge of *members* (key to evidence chunk count), with its ID and canonical key."""
    if len(members) < 2:
        raise ValueError("a merge needs at least two members")
    if (rule is None) == (decision is None):
        raise ValueError("a merge names a rule or a decision, not both")
    basis = rule if rule is not None else str(decision)
    return MergeEvent(
        merge_id=_merge_id(members, method, basis), label=label,
        members=MappingProxyType(dict(sorted(members.items()))),
        canonical=canonical_key(members), method=method, rule=rule, decision=decision,
        evidence=tuple(evidence), prompt_version=prompt_version, stamp=stamp,
    )


def split_event(merge_id: str, method: Method, reason: str, stamp: Stamp) -> SplitEvent:
    if not reason.strip():
        raise ValueError("a split needs a reason")
    return SplitEvent(merge_id=merge_id, method=method, reason=reason, stamp=stamp)


def _active(events: Iterable[LogEvent]) -> list[MergeEvent]:
    merges: dict[str, MergeEvent] = {}
    split: set[str] = set()
    for event in events:
        if isinstance(event, MergeEvent):
            if event.merge_id in merges:
                raise LogError(f"merge {event.merge_id} is logged twice")
            merges[event.merge_id] = event
        elif event.merge_id not in merges:
            raise LogError(f"split of unknown merge {event.merge_id}")
        elif event.merge_id in split:
            raise LogError(f"merge {event.merge_id} is already split")
        else:
            split.add(event.merge_id)
    return [m for m in merges.values() if m.merge_id not in split]


def replay(events: Iterable[LogEvent]) -> dict[str, str]:
    """The alias map: each key in an active merge to its group's canonical key, by key."""
    active = _active(events)
    counts: dict[str, int] = {}
    for merge in active:
        counts.update(merge.members)
    aliases: dict[str, str] = {}
    for group in connected(m.members for m in active):
        canonical = canonical_key({k: counts[k] for k in group})
        aliases.update(dict.fromkeys(group, canonical))
    return dict(sorted(aliases.items()))


def new_merges(log: Iterable[LogEvent], proposed: Iterable[MergeEvent]) -> tuple[MergeEvent, ...]:
    """The merges in *proposed* whose IDs the log does not hold yet, each once."""
    seen = {e.merge_id for e in log}
    fresh: dict[str, MergeEvent] = {}
    for merge in proposed:
        if merge.merge_id not in seen:
            fresh.setdefault(merge.merge_id, merge)
    return tuple(fresh.values())


# ── File format ───────────────────────────────────────────────────────────────

def _stamp_line(stamp: Stamp) -> dict[str, str]:
    return {"config_hash": stamp.config_hash, "commit": stamp.commit,
            "timestamp": stamp.timestamp}


def to_line(event: LogEvent) -> dict[str, object]:
    if isinstance(event, SplitEvent):
        return {"event": SPLIT, "merge_id": event.merge_id, "method": event.method.value,
                "reason": event.reason, **_stamp_line(event.stamp)}
    return {
        "event": MERGE, "merge_id": event.merge_id, "label": event.label,
        "members": dict(event.members), "canonical": event.canonical,
        "method": event.method.value, "rule": event.rule, "decision": event.decision,
        "evidence": [{"key": q.key, "name": q.name, "chunk_id": q.chunk_id, "quote": q.quote}
                     for q in event.evidence],
        "prompt_version": event.prompt_version, **_stamp_line(event.stamp),
    }


def from_line(line: Mapping[str, object]) -> LogEvent:
    stamp = Stamp(str(line["config_hash"]), str(line["commit"]), str(line["timestamp"]))
    method = Method(line["method"])
    if line["event"] == SPLIT:
        return split_event(str(line["merge_id"]), method, str(line["reason"]), stamp)
    if line["event"] != MERGE:
        raise ValueError(f"unknown event {line['event']!r}")
    members = line["members"]
    evidence = line["evidence"]
    if not isinstance(members, Mapping) or not isinstance(evidence, list):
        raise TypeError("members must be an object and evidence a list")
    event = merge_event(
        label=str(line["label"]), members={str(k): int(v) for k, v in members.items()},
        method=method, rule=_optional(line["rule"]), decision=_optional(line["decision"]),
        evidence=[EvidenceQuote(str(q["key"]), str(q["name"]), _optional(q["chunk_id"]),
                                str(q["quote"])) for q in evidence],
        prompt_version=_optional(line["prompt_version"]), stamp=stamp,
    )
    if event.merge_id != line["merge_id"] or event.canonical != line["canonical"]:
        raise ValueError("merge_id or canonical does not match the members")
    return event


def _optional(value: object) -> str | None:
    return None if value is None else str(value)


def read_log(path: Path) -> tuple[LogEvent, ...]:
    """The events in *path*, oldest first; an absent file is an empty log."""
    if not path.exists():
        return ()
    events = []
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            line = json.loads(text)
            if not isinstance(line, dict):
                raise TypeError("not an object")
            events.append(from_line(line))
        except (ValueError, KeyError, TypeError) as exc:
            raise LogError(f"{path.name} line {number} is not a log event: {exc}") from exc
    return tuple(events)


def append_events(path: Path, events: Sequence[LogEvent]) -> None:
    """Append *events* to *path*, one JSON line each, leaving earlier lines untouched."""
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(to_line(event), ensure_ascii=False, sort_keys=True) + "\n")
