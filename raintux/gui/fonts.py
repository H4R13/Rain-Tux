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


# Windows fonts that skins name, with free look-alikes (metric-compatible where they exist).
FONT_ALIASES: dict[str, list[str]] = {
    "Segoe UI": ["Selawik", "Noto Sans", "Open Sans", "Cantarell", "Ubuntu", "Liberation Sans", "DejaVu Sans"],
    "Segoe UI Light": ["Selawik Light", "Noto Sans Light", "Open Sans Light", "Noto Sans", "DejaVu Sans"],
    "Segoe UI Semilight": ["Selawik Semilight", "Noto Sans", "Open Sans", "DejaVu Sans"],
    "Segoe UI Semibold": ["Selawik Semibold", "Noto Sans SemiBold", "Open Sans SemiBold", "Noto Sans", "DejaVu Sans"],
    "Segoe UI Black": ["Noto Sans Black", "Open Sans ExtraBold", "DejaVu Sans"],
    "Segoe UI Symbol": ["Noto Sans Symbols", "Noto Sans Symbols2", "DejaVu Sans"],
    "Segoe UI Emoji": ["Noto Color Emoji", "Twemoji", "DejaVu Sans"],
    "Segoe MDL2 Assets": ["Noto Sans Symbols2", "DejaVu Sans"],
    "Segoe Fluent Icons": ["Noto Sans Symbols2", "DejaVu Sans"],
    "Segoe Print": ["Comic Neue", "DejaVu Sans"],
    "Trebuchet MS": ["Ubuntu", "Noto Sans", "Fira Sans", "Liberation Sans", "DejaVu Sans"],
    "Tahoma": ["DejaVu Sans Condensed", "Noto Sans", "Liberation Sans", "DejaVu Sans"],
    "Verdana": ["DejaVu Sans", "Noto Sans"],
    "Calibri": ["Carlito", "Noto Sans", "Liberation Sans", "DejaVu Sans"],
    "Calibri Light": ["Carlito", "Noto Sans Light", "DejaVu Sans"],
    "Cambria": ["Caladea", "Noto Serif", "Liberation Serif", "DejaVu Serif"],
    "Candara": ["Noto Sans", "Ubuntu", "DejaVu Sans"],
    "Consolas": ["Inconsolata", "Noto Sans Mono", "Liberation Mono", "DejaVu Sans Mono"],
    "Lucida Console": ["Noto Sans Mono", "Liberation Mono", "DejaVu Sans Mono"],
    "Lucida Sans Unicode": ["Noto Sans", "DejaVu Sans"],
    "Century Gothic": ["URW Gothic", "TeX Gyre Adventor", "Noto Sans", "DejaVu Sans"],
    "Franklin Gothic Medium": ["Libre Franklin", "Noto Sans", "DejaVu Sans"],
    "Gill Sans MT": ["Gillius ADF", "Noto Sans", "DejaVu Sans"],
    "Bahnschrift": ["DIN Alternate", "Noto Sans Condensed", "DejaVu Sans Condensed"],
    "Impact": ["Anton", "Oswald", "Liberation Sans Narrow", "DejaVu Sans Condensed"],
    "Georgia": ["Gelasio", "Noto Serif", "DejaVu Serif"],
    "Palatino Linotype": ["TeX Gyre Pagella", "URW Palladio L", "Noto Serif", "DejaVu Serif"],
    "Book Antiqua": ["TeX Gyre Pagella", "Noto Serif", "DejaVu Serif"],
    "Microsoft YaHei": ["Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei"],
    "Microsoft JhengHei": ["Noto Sans CJK TC", "Source Han Sans TC"],
    "Meiryo": ["Noto Sans CJK JP", "Source Han Sans JP"],
    "Malgun Gothic": ["Noto Sans CJK KR", "Source Han Sans KR"],
}


def _alias_xml() -> bytes:
    from xml.sax.saxutils import escape

    parts = ['<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>']
    for family, prefer in FONT_ALIASES.items():
        fams = "".join(f"<family>{escape(f)}</family>" for f in prefer)
        parts.append(f'<alias binding="same"><family>{escape(family)}</family><prefer>{fams}</prefer></alias>')
    parts.append("</fontconfig>")
    return "".join(parts).encode()


def install_font_aliases() -> bool:
    """Teach this process's fontconfig to substitute common Windows fonts sensibly."""
    lib = _fontconfig()
    if lib is None or not hasattr(lib, "FcConfigParseAndLoadFromMemory"):
        return False
    try:
        lib.FcConfigGetCurrent.restype = ctypes.c_void_p
        lib.FcConfigParseAndLoadFromMemory.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.FcConfigParseAndLoadFromMemory.restype = ctypes.c_int
        config = lib.FcConfigGetCurrent()
        if not config:
            return False
        ok = bool(lib.FcConfigParseAndLoadFromMemory(config, _alias_xml(), 0))
    except Exception:
        log.debug("font aliases not installed", exc_info=True)
        return False
    if ok:
        from raintux.meters.text import reset_font_cache

        reset_font_cache()
    return ok
