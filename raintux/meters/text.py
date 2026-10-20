"""Pango text layout for String meters (with a rough fallback when Pango is unavailable)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

try:  # pragma: no cover - depends on system packages
    import gi

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    HAVE_PANGO = True
except (ImportError, ValueError):  # pragma: no cover
    Pango = PangoCairo = None  # type: ignore[assignment]
    HAVE_PANGO = False

_measure_ctx: Any = None
_font_options: Any = None
_measured: dict[tuple, tuple[float, float]] = {}  # (text, style) -> measure(); cleared when fonts change
# Pango.Weight's named values: PyGObject accepts only these, while skins write any 1..999
# (DirectWrite takes them all; a variable font shows 650 between 600 and 700).
_WEIGHTS = (100, 200, 300, 350, 380, 400, 500, 600, 700, 800, 900, 1000)


def pango_weight(weight: float) -> Any:
    """The nearest Pango.Weight (ties go to the heavier one)."""
    w = max(100, min(1000, int(weight)))
    return Pango.Weight(min(_WEIGHTS, key=lambda v: (abs(v - w), -v)))


@dataclass
class TextStyle:
    face: str = "Arial"
    size: float = 10.0  # points (Rainmeter FontSize)
    weight: int = 400
    italic: bool = False
    color: tuple[float, float, float, float] = (1, 1, 1, 1)
    h_align: str = "left"  # left | center | right
    v_align: str = "top"  # top | center | bottom
    width: float | None = None  # wrap / clip width (content px)
    height: float | None = None
    wrap: bool = False
    ellipsize: bool = False
    # (start_char, end_char, kind, value) inline settings
    inline: list[tuple[int, int, str, Any]] = field(default_factory=list)


def _options() -> Any:
    global _font_options
    if _font_options is None:
        import cairo

        fo = cairo.FontOptions()
        fo.set_antialias(cairo.ANTIALIAS_GRAY)
        fo.set_hint_metrics(cairo.HINT_METRICS_OFF)
        fo.set_hint_style(cairo.HINT_STYLE_SLIGHT)
        _font_options = fo
    return _font_options


def _ctx() -> Any:
    global _measure_ctx
    if _measure_ctx is None:
        import cairo

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        _measure_ctx = cairo.Context(surf)
    return _measure_ctx


def reset_font_cache() -> None:
    """Call after registering new fonts so Pango sees them.

    A fontconfig-based font map keeps its font set (a new font map does too), so it has to be
    told: ``pango_fc_font_map_config_changed``, which PyGObject doesn't expose (no PangoFc).
    """
    global _measure_ctx
    _measure_ctx = None
    _measured.clear()
    if not HAVE_PANGO:
        return
    try:
        import ctypes
        import ctypes.util

        fontmap = PangoCairo.FontMap.get_default()
        lib = ctypes.util.find_library("pangoft2-1.0")
        if lib is None or "Fc" not in type(fontmap).__name__:
            return
        capsule = ctypes.pythonapi.PyCapsule_GetPointer
        capsule.restype = ctypes.c_void_p
        capsule.argtypes = [ctypes.py_object, ctypes.c_char_p]
        changed = ctypes.CDLL(lib).pango_fc_font_map_config_changed
        changed.argtypes = [ctypes.c_void_p]
        changed(capsule(fontmap.__gpointer__, None))
    except Exception:  # pragma: no cover
        log.debug("could not refresh Pango's font map", exc_info=True)


def _byte_index(text: str, char_index: int) -> int:
    return len(text[:char_index].encode("utf-8"))


def make_layout(cr: Any, text: str, style: TextStyle) -> Any:
    layout = PangoCairo.create_layout(cr)
    PangoCairo.context_set_font_options(layout.get_context(), _options())
    PangoCairo.context_set_resolution(layout.get_context(), 96.0)
    layout.context_changed()
    desc = Pango.FontDescription()
    desc.set_family(style.face or "Sans")
    desc.set_size(max(1, int(style.size * Pango.SCALE)))
    desc.set_weight(pango_weight(style.weight))
    if style.italic:
        desc.set_style(Pango.Style.ITALIC)
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    layout.set_alignment(
        {"center": Pango.Alignment.CENTER, "right": Pango.Alignment.RIGHT}.get(style.h_align, Pango.Alignment.LEFT)
    )
    if style.width is not None and style.width > 0 and (style.wrap or style.ellipsize or style.h_align != "left"):
        layout.set_width(int(style.width * Pango.SCALE))
        if style.wrap:
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        if style.ellipsize:
            if style.height is not None and style.height > 0:
                layout.set_ellipsize(Pango.EllipsizeMode.END)
                layout.set_height(int(style.height * Pango.SCALE))
            elif not style.wrap:
                # Single line clipped at the width.
                layout.set_ellipsize(Pango.EllipsizeMode.END)
                layout.set_height(-1)
            # Wrapping with no height limit: the text grows downwards, nothing to ellipsize.
    if style.inline:
        layout.set_attributes(_attributes(text, style.inline))
    return layout


def _attributes(text: str, inline: list[tuple[int, int, str, Any]]) -> Any:
    attrs = Pango.AttrList()
    for start, end, kind, value in inline:
        a = None
        extra = []
        if kind == "color":
            r, g, b, al = value
            a = Pango.attr_foreground_new(int(r * 65535), int(g * 65535), int(b * 65535))
            extra.append(Pango.attr_foreground_alpha_new(max(1, int(al * 65535))))
        elif kind == "face":
            a = Pango.attr_family_new(value)
        elif kind == "size":
            a = Pango.attr_size_new(int(value * Pango.SCALE))
        elif kind == "weight":
            a = Pango.attr_weight_new(pango_weight(value))
        elif kind == "italic":
            a = Pango.attr_style_new(Pango.Style.ITALIC)
        elif kind == "oblique":
            a = Pango.attr_style_new(Pango.Style.OBLIQUE)
        elif kind == "underline":
            a = Pango.attr_underline_new(Pango.Underline.SINGLE)
        elif kind == "strikethrough":
            a = Pango.attr_strikethrough_new(True)
        elif kind == "spacing":
            a = Pango.attr_letter_spacing_new(int(value * Pango.SCALE))
        elif kind == "stretch":
            a = Pango.attr_stretch_new(Pango.Stretch(int(value)))
        elif kind == "rise":
            a = Pango.attr_rise_new(int(value * Pango.SCALE))
        elif kind == "scale":
            a = Pango.attr_scale_new(float(value))
        for attr in [a, *extra]:
            if attr is None:
                continue
            attr.start_index = _byte_index(text, start)
            attr.end_index = _byte_index(text, end)
            attrs.insert(attr)
    return attrs


def measure(text: str, style: TextStyle) -> tuple[float, float]:
    """Logical size in pixels of ``text`` laid out with ``style``."""
    if not HAVE_PANGO:
        lines = text.split("\n") or [""]
        px = style.size * 96 / 72
        w = max(len(line) for line in lines) * px * 0.55
        if style.width and (style.wrap or style.ellipsize):
            w = min(w, style.width)
        return (w, len(lines) * px * 1.2)
    key = (text, style.face, style.size, style.weight, style.italic, style.h_align, style.width, style.height,
           style.wrap, style.ellipsize, tuple((a, b, k, v if not isinstance(v, list) else tuple(v))
                                                for a, b, k, v in style.inline))
    hit = _measured.get(key)
    if hit is not None:
        return hit
    layout = make_layout(_ctx(), text, style)
    _ink, logical = layout.get_pixel_extents()
    w = float(logical.width)
    if style.width is not None and style.width > 0 and layout.get_width() > 0:
        w = min(max(w, 0.0), style.width) if style.h_align == "left" else style.width
    if len(_measured) > 20000:
        _measured.clear()
    _measured[key] = (w, float(logical.height))
    return _measured[key]


def draw(cr: Any, text: str, style: TextStyle, x: float, y: float, w: float, h: float, effect: str = "",
         effect_color: tuple[float, float, float, float] = (0, 0, 0, 1),
         shadows: list[tuple[int, int, float, float, tuple[float, float, float, float]]] | None = None) -> None:
    """Draw text in the box (x, y, w, h); ``effect`` is ``shadow`` or ``border``."""
    if not HAVE_PANGO:
        return
    layout = make_layout(cr, text, style)
    if layout.get_width() < 0 and style.h_align != "left":
        # Unconstrained width: align the single block inside the box ourselves.
        _ink, logical = layout.get_pixel_extents()
        offset = (w - logical.width) / 2 if style.h_align == "center" else (w - logical.width)
        x += offset
    _ink, logical = layout.get_pixel_extents()
    if style.v_align != "top" and h > logical.height:
        y += (h - logical.height) / 2 if style.v_align == "center" else (h - logical.height)
    x -= logical.x if layout.get_width() < 0 else 0
    if effect == "shadow":
        cr.save()
        cr.move_to(x + 1, y + 1)
        cr.set_source_rgba(*effect_color)
        PangoCairo.show_layout(cr, layout)
        cr.restore()
    elif effect == "border":
        cr.save()
        cr.move_to(x, y)
        PangoCairo.layout_path(cr, layout)
        cr.set_source_rgba(*effect_color)
        cr.set_line_width(2.0)
        import cairo

        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()
        cr.restore()
    for start, end, sx, sy, color in shadows or []:
        _draw_range_shadow(cr, text, style, layout, start, end, x + sx, y + sy, color)
    cr.save()
    cr.move_to(x, y)
    cr.set_source_rgba(*style.color)
    PangoCairo.show_layout(cr, layout)
    cr.restore()


def _draw_range_shadow(cr: Any, text: str, style: TextStyle, layout: Any, start: int, end: int,
                       x: float, y: float, color: tuple[float, float, float, float]) -> None:
    """InlineSetting=Shadow: redraw only the matched range, offset, in the shadow colour."""
    shadow = layout.copy()
    # Keep the inline sizes/faces; only the colours change (later attributes win).
    base = layout.get_attributes()
    attrs = base.copy() if base is not None else Pango.AttrList()
    hide = Pango.attr_foreground_alpha_new(1)
    hide.start_index, hide.end_index = 0, len(text.encode("utf-8"))
    attrs.insert(hide)
    r, g, b, a = color
    show = Pango.attr_foreground_new(int(r * 65535), int(g * 65535), int(b * 65535))
    show_a = Pango.attr_foreground_alpha_new(max(1, int(a * 65535)))
    for at in (show, show_a):
        at.start_index, at.end_index = _byte_index(text, start), _byte_index(text, end)
        attrs.insert(at)
    shadow.set_attributes(attrs)
    cr.save()
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, shadow)
    cr.restore()


_WORD = re.compile(r"\b(\w)(\w*)", re.UNICODE)


def apply_case(text: str, case: str) -> str:
    c = case.strip().lower()
    if c == "upper":
        return text.upper()
    if c == "lower":
        return text.lower()
    if c == "proper":
        return _WORD.sub(lambda m: m.group(1).upper() + m.group(2).lower(), text)
    return text
