"""Decode Rainmeter ``.ini`` / ``.inc`` files (UTF-8, UTF-16 with BOM, or ANSI)."""

from __future__ import annotations

from pathlib import Path

from raintux.core.ini_writer import detect_encoding


def normalize_text(text: str) -> str:
    """Strip a BOM and normalize newlines.

    Values are left untouched: backslashes are meaningful in regular expressions
    (``RegExp=``, ``Substitute=``) and file paths are normalized where they are used.
    """
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_ini_normalized(path: Path) -> str:
    """Read a skin file in whatever encoding Rainmeter would accept."""
    raw = path.read_bytes()
    return normalize_text(raw.decode(detect_encoding(raw), errors="replace"))
