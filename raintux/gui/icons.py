"""File-type icons as PNG paths (for FileView ``Type=Icon``)."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)
_cache: dict[str, str] = {}


def _cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    d = base / "raintux" / "icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def icon_png_for(path: Path, size: int = 48) -> str:
    """PNG of the theme icon GTK would show for ``path`` (empty string when unavailable)."""
    try:
        from gi.repository import Gdk, GdkPixbuf, Gio, Gtk
    except ImportError:
        return ""
    try:
        info = Gio.File.new_for_path(str(path)).query_info("standard::icon", Gio.FileQueryInfoFlags.NONE, None)
        gicon = info.get_icon()
        key = f"{gicon.to_string()}@{size}"
        if key in _cache:
            return _cache[key]
        display = Gdk.Display.get_default()
        if display is None:
            return ""
        theme = Gtk.IconTheme.get_for_display(display)
        paintable = theme.lookup_by_gicon(gicon, size, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.FORCE_REGULAR)
        f = paintable.get_file()
        src = f.get_path() if f is not None else None
        if not src:
            return ""
        if src.lower().endswith(".png"):
            out = src
        else:
            out = str(_cache_dir() / (hashlib.sha1(key.encode()).hexdigest() + ".png"))
            if not Path(out).exists():
                GdkPixbuf.Pixbuf.new_from_file_at_size(src, size, size).savev(out, "png", [], [])
        _cache[key] = out
        return out
    except Exception:
        log.debug("icon lookup failed for %s", path, exc_info=True)
        return ""
