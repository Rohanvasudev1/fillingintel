"""Write a JSON file atomically, for the on-disk caches."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path


def write_json_atomic(
    path: Path, body: Mapping[str, object], indent: int | None = None
) -> None:
    """Write *body* as JSON to *path* through a temporary file in the same directory,
    so a crash never leaves a truncated file behind.  On failure the temporary file
    is removed and the error is raised.  *indent* is passed to ``json.dump``."""
    write_text_atomic(path, json.dumps(body, indent=indent))


def write_text_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* as write_json_atomic writes JSON: whole, or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as fh:
        tmp = Path(fh.name)
        try:
            fh.write(text)
        except OSError:
            fh.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def write_json_once(path: Path, body: Mapping[str, object]) -> bool:
    """Write *body* to *path* whole, as write_json_atomic does, unless *path* exists.

    The file is linked into place only when the name is free, so a second writer
    never replaces the first one's file.  Returns True when this call wrote it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as fh:
        tmp = Path(fh.name)
    try:
        tmp.write_text(json.dumps(body), encoding="utf-8")
        os.link(tmp, path)
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)
    return True
