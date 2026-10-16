"""Path helpers for skins written on Windows (case-insensitive file names, backslashes)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def windows_to_posix(value: str) -> str:
    """``C:\\x\\y`` / ``x\\y`` → forward slashes; a drive letter is dropped (no drives on Linux)."""
    p = value.strip().strip('"').replace("\\", "/")
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        p = p[2:]
    return p


@lru_cache(maxsize=4096)
def _ci_cached(path_str: str, _stamp: int) -> str:
    path = Path(path_str)
    if path.exists() or not path.is_absolute():
        return path_str
    parts = path.parts
    cur = Path(parts[0])
    for i, part in enumerate(parts[1:], start=1):
        nxt = cur / part
        if nxt.exists():
            cur = nxt
            continue
        try:
            low = part.lower()
            match = next((e.name for e in os.scandir(cur) if e.name.lower() == low), None)
        except OSError:
            match = None
        if match is None:
            # Keep the rest as written; nothing further can match.
            return str(cur.joinpath(part, *parts[i + 1:]))
        cur = cur / match
    return str(cur)


def resolve_ci(path: Path) -> Path:
    """The existing file whose path matches ``path`` ignoring case, or ``path`` unchanged.

    Windows is case-insensitive, so skins often refer to ``Images\\BG.PNG`` when the file is
    ``images/bg.png``. Results are cached for a few seconds to keep redraws cheap.
    """
    import time

    return Path(_ci_cached(str(path), int(time.monotonic() // 5)))
