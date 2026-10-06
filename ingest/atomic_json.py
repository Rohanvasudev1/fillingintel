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
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as fh:
        tmp = Path(fh.name)
        try:
            json.dump(body, fh, indent=indent)
        except (TypeError, ValueError, OSError):
            fh.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
