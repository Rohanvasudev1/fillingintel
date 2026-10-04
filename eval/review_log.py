"""The review log, ``eval/review_log.jsonl``: one human decision per agent draft.

Entries are keyed by ``draft_key`` (question plus gold chunks), not by draft
ID, so they survive re-merging the drafts.  Unreadable lines (a write cut
short) are skipped, never fatal.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ACCEPT = "accept"
REJECT = "reject"


def read_decisions(log: Path) -> dict[str, str]:
    """``{draft_key: decision}``; a later entry for the same draft wins."""
    if not log.exists():
        return {}
    decisions: dict[str, str] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            decisions[str(entry["draft_key"])] = str(entry["decision"])
        except (ValueError, KeyError, TypeError):
            logger.warning("skipping unreadable review-log line in %s: %.80s", log, line)
            continue
    return decisions


def accepted_keys(log: Path) -> set[str]:
    return {key for key, decision in read_decisions(log).items() if decision == ACCEPT}


def append_decision(log: Path, key: str, draft_id: str, decision: str, reviewer: str) -> None:
    entry = {
        "draft_key": key,
        "draft_id": draft_id,
        "decision": decision,
        "reviewer": reviewer,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
