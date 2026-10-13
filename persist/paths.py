"""Where persistence linking writes, and the link file (Step 10).

Outputs live under `benchmarks/persistence/` and are committed: `links.jsonl`,
the current selection, one link per line sorted by later key, rewritten by
each complete run; and one report per run, `run{N}.json`.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from ingest.provenance import REPO_ROOT
from persist.links import PersistLink

OUTPUT_DIR = REPO_ROOT / "benchmarks" / "persistence"
LINKS_NAME = "links.jsonl"
LINKS = OUTPUT_DIR / LINKS_NAME


def read_links(path: Path) -> tuple[PersistLink, ...]:
    """The links in *path*; ValueError names the first bad line."""
    return parse_links(path.read_bytes(), path.name)


def parse_links(raw: bytes, name: str) -> tuple[PersistLink, ...]:
    """The links in a link file's bytes, so a caller can hash the same bytes it parsed;
    ValueError names the first bad line."""
    links = []
    for number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            links.append(PersistLink.from_dict(data))
        except ValueError as exc:
            raise ValueError(f"{name} line {number}: {exc}") from exc
    return tuple(links)


def write_links(path: Path, links: Iterable[PersistLink]) -> None:
    """Write *links* sorted by later key, through a temporary file so a crash leaves the old
    file whole."""
    lines = [json.dumps(link.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
             for link in sorted(links, key=lambda lk: lk.later_key)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as fh:
        tmp = Path(fh.name)
        try:
            fh.writelines(lines)
        except OSError:
            fh.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
