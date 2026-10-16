"""``!WriteKeyValue``: edit one key of an INI file in place (encoding, order and comments kept)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
WRITABLE_SUFFIXES = (".ini", ".inc", ".txt")


def detect_encoding(raw: bytes) -> str:
    """Rainmeter skins are UTF-8 (maybe with BOM) or UTF-16 LE with BOM; old ones ANSI."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def write_key_value(path: Path, section: str, key: str, value: str) -> bool:
    """Set ``key=value`` in ``[section]`` of ``path``; adds the key or section when missing.

    Only ``.ini``, ``.inc`` and ``.txt`` files are writable, as in Rainmeter.
    """
    path = Path(path)
    if path.suffix.lower() not in WRITABLE_SUFFIXES:
        log.warning("!WriteKeyValue refused for %s (only %s)", path, ", ".join(WRITABLE_SUFFIXES))
        return False
    raw = path.read_bytes() if path.is_file() else b""
    enc = detect_encoding(raw) if raw else "utf-8"
    text = raw.decode(enc, errors="replace") if raw else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    sec_l, key_l = section.strip().lower(), key.strip().lower()
    start: int | None = None
    end: int | None = None
    for i, line in enumerate(lines):
        m = _SECTION.match(line)
        if not m:
            continue
        if start is not None and end is None:
            end = i
        if m.group(1).strip().lower() == sec_l and start is None:
            start, end = i, None
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]", f"{key}={value}"]
    else:
        stop = len(lines) if end is None else end
        for i in range(start + 1, stop):
            if lines[i].lstrip().startswith(";"):
                continue
            k, sep, _old = lines[i].partition("=")
            if sep and k.strip().lower() == key_l:
                lines[i] = f"{k.rstrip()}={value}"
                break
        else:
            insert = stop
            while insert > start + 1 and not lines[insert - 1].strip():
                insert -= 1
            lines.insert(insert, f"{key}={value}")
    path.write_bytes((newline.join(lines) + newline).encode(enc))
    return True
