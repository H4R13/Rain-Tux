"""Let the pure layout/parser code be imported on machines without GTK or pycairo."""

from __future__ import annotations

import importlib
import sys
import types
from unittest import mock


def _missing(name: str) -> bool:
    try:
        if name.startswith("gi.repository."):
            import gi

            gi.require_version(name.rsplit(".", 1)[1], "1.0")
        importlib.import_module(name)
    except (ImportError, ValueError):
        return True
    return False


if _missing("cairo"):
    sys.modules["cairo"] = mock.MagicMock(name="cairo")

if _missing("gi.repository.Pango"):
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_a, **_k: None  # type: ignore[attr-defined]
    repo = mock.MagicMock(name="gi.repository")
    gi.repository = repo  # type: ignore[attr-defined]
    sys.modules.update({"gi": gi, "gi.repository": repo})
