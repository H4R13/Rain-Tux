"""Path helpers for skins written on Windows (case-insensitive file names, backslashes)."""

from __future__ import annotations

import os
import re
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


_ENV_REF = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%")


@lru_cache(maxsize=1)
def _windows_environment() -> dict[str, str]:
    """Windows variables skins use, mapped to this system (real environment variables win)."""
    import getpass
    import socket
    import tempfile

    home = str(Path.home())
    env = {
        "SYSTEMDRIVE": "C:",  # FreeDiskSpace maps C: to /
        "HOMEDRIVE": "C:",
        "USERPROFILE": home,
        "HOMEPATH": home,
        "APPDATA": os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"),
        "LOCALAPPDATA": os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"),
        "TEMP": tempfile.gettempdir(),
        "TMP": tempfile.gettempdir(),
        "NUMBER_OF_PROCESSORS": str(os.cpu_count() or 1),
    }
    try:
        env["USERNAME"] = getpass.getuser()
        env["COMPUTERNAME"] = socket.gethostname()
    except (OSError, KeyError):
        pass
    env.update({k.upper(): v for k, v in os.environ.items()})
    return env


def environment_variable(name: str) -> str | None:
    """``%NAME%`` / ``[%NAME]``: case-insensitive, like Windows."""
    return _windows_environment().get(name.upper())


def expand_environment(text: str) -> str:
    """Rainmeter expands ``%NAME%`` in option values (ExpandEnvironmentStrings); unknown ones stay."""
    if "%" not in text:
        return text
    return _ENV_REF.sub(lambda m: environment_variable(m.group(1)) or m.group(0), text)
