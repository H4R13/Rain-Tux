"""Small GTK version shims (Ubuntu 24.04 ships GTK 4.14; older 4.x lacks a few calls)."""

from __future__ import annotations

from typing import Any


def load_css(provider: Any, css: str) -> None:
    """``Gtk.CssProvider.load_from_string`` (GTK 4.12+) with a fallback for older GTK 4."""
    if hasattr(provider, "load_from_string"):
        provider.load_from_string(css)
    else:
        provider.load_from_data(css.encode(), -1)
