"""Make skin fonts available to Pango: ``@Resources/Fonts`` and ``[Rainmeter] LocalFont=``."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from raintux.core.skin import Skin

log = logging.getLogger(__name__)

_FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc", ".pfb", ".woff", ".woff2")
_registered: set[str] = set()
_fc: Any = None


def _fontconfig() -> Any:
    global _fc
    if _fc is None:
        name = ctypes.util.find_library("fontconfig") or "libfontconfig.so.1"
        try:
            lib = ctypes.CDLL(name)
            lib.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            lib.FcConfigAppFontAddFile.restype = ctypes.c_int
            _fc = lib
        except OSError:
            log.info("fontconfig not found; skin fonts will not be loaded")
            _fc = False
    return _fc or None


def register_font(path: Path) -> bool:
    """Add one font file to this process's fontconfig set. Returns True when newly added."""
    key = str(path.resolve())
    if key in _registered or not path.is_file():
        return False
    lib = _fontconfig()
    if lib is None:
        return False
    ok = bool(lib.FcConfigAppFontAddFile(None, key.encode()))
    if ok:
        _registered.add(key)
        log.debug("registered font %s", key)
    return ok


def register_skin_fonts(skin: Skin) -> None:
    """Fonts in the root config's ``@Resources/Fonts`` plus ``LocalFont``, ``LocalFont2``… options."""
    files: list[Path] = []
    fonts_dir = skin.resources / "Fonts"
    if fonts_dir.is_dir():
        files += [p for p in sorted(fonts_dir.rglob("*")) if p.suffix.lower() in _FONT_SUFFIXES]
    i = 1
    while True:
        key = "localfont" if i == 1 else f"localfont{i}"
        if not skin.rm.has(key):
            break
        files.append(skin.path(skin.rm.get(key)))
        i += 1
    added = [f for f in files if register_font(f)]
    if added:
        from raintux.meters.text import reset_font_cache

        reset_font_cache()
