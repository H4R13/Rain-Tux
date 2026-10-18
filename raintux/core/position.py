"""Skin window positions the way Rainmeter reads them: WindowX, WindowY, AnchorX, AnchorY.

After ``#variables#`` are replaced, Rainmeter (Library/SkinPosition.cpp and
ConfigParser::ParseFormulaWithModifiers) accepts:

* ``(formula)`` followed by modifiers: everything up to the last ``)`` is evaluated;
* ``R`` (WindowX) / ``B`` (WindowY): measured from the right / bottom edge of the monitor;
* ``%``: a percentage of the monitor's width / height;
* ``@N``: relative to monitor N (``@0``: the whole desktop); the other axis follows it;
* AnchorX / AnchorY: the point of the skin that goes there, in pixels, ``%`` of the skin's size,
  or measured from its right / bottom edge with ``R`` / ``B``.

Without ``@N`` a position is relative to the primary monitor, and Rainmeter's coordinates have
the primary monitor at 0,0 (Windows always does). Here the first monitor is the primary one;
positions are worked out in that space and returned as desktop coordinates.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from raintux.core.formula import FormulaError, evaluate, parse_number
from raintux.core.skin_parser import expand_variables

Rect = tuple[int, int, int, int]  # x, y, width, height
FALLBACK_MONITORS: tuple[Rect, ...] = ((0, 0, 1920, 1080),)

_NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)")


def _rainmeter_space(monitors: Sequence[Rect]) -> tuple[list[Rect], tuple[int, int]]:
    """Monitor rects moved so the primary one is at 0,0, and the primary's desktop origin."""
    mons = list(monitors) or list(FALLBACK_MONITORS)
    px, py = mons[0][0], mons[0][1]
    return [(x - px, y - py, w, h) for x, y, w, h in mons], (px, py)


def _bounds(rects: Sequence[Rect]) -> Rect:
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    return (x0, y0, max(r[0] + r[2] for r in rects) - x0, max(r[1] + r[3] for r in rects) - y0)


def monitor_variables(monitors: Sequence[Rect]) -> dict[str, str]:
    """``#SCREENAREAX#``, ``#WORKAREAWIDTH@2#``… as Rainmeter.ini sees them (unnumbered: the primary)."""
    rects, _origin = _rainmeter_space(monitors)
    out: dict[str, str] = {}

    def put(prefix: str, r: Rect, suffix: str = "") -> None:
        for name, v in zip(("X", "Y", "WIDTH", "HEIGHT"), r):
            out[f"{prefix}{name}{suffix}"] = str(v)

    for area in ("SCREENAREA", "WORKAREA"):
        put(area, rects[0])
        put("P" + area, rects[0])
        for i, r in enumerate(rects, start=1):
            put(area, r, f"@{i}")
    put("VSCREENAREA", _bounds(rects))
    return out


def _format(value: float) -> str:
    """Rainmeter's ``%lf`` with the trailing zeros removed."""
    s = f"{value:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def with_modifiers(text: str) -> str:
    """ParseFormulaWithModifiers: evaluate ``(formula)`` and keep what follows its last ``)``."""
    pos = text.rfind(")")
    if pos < 0 or not text.startswith("("):
        return text
    try:
        value = evaluate(text[:pos + 1])
    except (FormulaError, ArithmeticError, ValueError, TypeError):
        return text
    return _format(value) + text[pos + 1:]


def option_value(raw: str | None, monitors: Sequence[Rect] = (),
                 resolve: Callable[[str], str] | None = None) -> str:
    """A WindowX-style option as Rainmeter reads it: variables replaced, then the formula evaluated.

    ``resolve`` expands what's left (a skin's own variables) after the monitor variables.
    """
    text = expand_variables((raw or "").strip(), monitor_variables(monitors))
    if resolve is not None:
        text = resolve(text)
    return with_modifiers(text.strip())


def _leading(text: str, chars: str) -> float:
    """``_wtof`` of the leading run of ``chars`` (Rainmeter reads the number this way)."""
    end = 0
    while end < len(text) and text[end] in chars:
        end += 1
    m = _NUMBER.match(text, 0, end)
    return float(m.group(0)) if m else 0.0


def _after_hash(text: str, ch: str) -> bool:
    """``ch`` is a modifier only after the last ``#`` (it may be part of a variable that was left)."""
    i = text.rfind(ch)
    h = text.rfind("#")
    return i >= 0 and (h < 0 or h < i)


def anchor_offset(option: str | None, size: float, zoom: float = 1.0, opposite: str = "R",
                  monitors: Sequence[Rect] = ()) -> int:
    """ParseAnchorOption: how far into the skin (from its left / top) the WindowX / WindowY point is."""
    text = option_value(option, monitors)
    n = _leading(text, "0123456789.")
    n = size * zoom * n / 100.0 if "%" in text else n * zoom
    if opposite in text:
        n = size * zoom - n
    return int(n)


class _Axis:
    def __init__(self, option: str, opposite: str, count: int) -> None:
        self.number = _leading(option, "-0123456789.")
        self.percentage = _after_hash(option, "%")
        self.opposite = _after_hash(option, opposite)
        self.monitor: int | None = None
        if _after_hash(option, "@"):
            m = re.match(r"\d+", option[option.rfind("@") + 1:])
            if m and int(m.group(0)) <= count:
                self.monitor = int(m.group(0))

    def resolve(self, origin: int, extent: int, anchor: int) -> int:
        offset = int(extent * (self.number / 100.0)) if self.percentage else int(self.number)
        return origin + (extent - offset if self.opposite else offset) - anchor


def resolve_position(window_x: str | None, window_y: str | None, anchor_x: str | None = "0",
                     anchor_y: str | None = "0", size: tuple[float, float] = (0, 0),
                     monitors: Sequence[Rect] = (), zoom: float = 1.0,
                     resolve: Callable[[str], str] | None = None) -> tuple[int, int]:
    """Desktop position of a skin's top-left corner for these options (Rainmeter's ResolveVirtualizedPosition)."""
    rects, (px, py) = _rainmeter_space(monitors)
    x = _Axis(option_value(window_x or "0", monitors, resolve), "R", len(rects))
    y = _Axis(option_value(window_y or "0", monitors, resolve), "B", len(rects))
    if x.monitor is not None and y.monitor is None:
        y.monitor = x.monitor
    elif y.monitor is not None and x.monitor is None:
        x.monitor = y.monitor

    def ref(monitor: int | None) -> Rect:
        if monitor == 0:
            return _bounds(rects)
        return rects[(monitor or 1) - 1]

    rx, ry = ref(x.monitor), ref(y.monitor)
    ax = anchor_offset(anchor_x, size[0], zoom, "R", monitors)
    ay = anchor_offset(anchor_y, size[1], zoom, "B", monitors)
    return px + x.resolve(rx[0], rx[2], ax), py + y.resolve(ry[1], ry[3], ay)


def authored_position(state: Any, size: tuple[float, float], monitors: Sequence[Rect],
                      zoom: float = 1.0) -> tuple[int, int] | None:
    """Where ``state.window_x`` / ``window_y`` (WindowX/WindowY as written) put the skin.

    None when neither is set, i.e. the skin was moved since and ``state.x`` / ``y`` hold its place.
    """
    if state.window_x is None and state.window_y is None:
        return None
    _rects, (px, py) = _rainmeter_space(monitors)
    wx = state.window_x if state.window_x is not None else str(state.x - px)
    wy = state.window_y if state.window_y is not None else str(state.y - py)
    return resolve_position(wx, wy, state.anchor_x, state.anchor_y, size, monitors, zoom)


# Options a skin's [Rainmeter] section can give as Default<Option> for its first load.
_DEFAULT_TEXT = (("WindowX", "window_x"), ("WindowY", "window_y"), ("AnchorX", "anchor_x"), ("AnchorY", "anchor_y"))
_DEFAULT_FLAGS = (("Draggable", "draggable"), ("SnapEdges", "snap_edges"), ("ClickThrough", "click_through"),
                  ("KeepOnScreen", "keep_on_screen"), ("SavePosition", "save_position"))


def apply_skin_defaults(state: Any, option: Callable[[str], str | None], monitors: Sequence[Rect] = (),
                        resolve: Callable[[str], str] | None = None) -> None:
    """DefaultWindowX, DefaultAnchorX, DefaultAlwaysOnTop… from a skin's [Rainmeter] section.

    Rainmeter reads these when a config has no settings yet (Skin::ReadOptions with isDefault).
    """
    for key, attr in _DEFAULT_TEXT:
        raw = option("Default" + key)
        if raw is not None:
            setattr(state, attr, option_value(raw, monitors, resolve))
    raw = option("DefaultAlwaysOnTop")
    if raw is not None:
        z = int(parse_number(resolve(raw) if resolve else raw, 0))
        state.z = z if -2 <= z <= 2 else 0
    for key, attr in _DEFAULT_FLAGS:
        raw = option("Default" + key)
        if raw is not None:
            setattr(state, attr, parse_number(resolve(raw) if resolve else raw, 0) != 0)


def layer_for_zpos(z: int) -> str:
    """Layer-shell layer for AlwaysOnTop -2..2. "On desktop" (-2) and "Bottom" (-1) both go on the
    bottom layer: the background layer is where wallpaper daemons (swaybg, hyprpaper, swww) draw,
    and a skin there is hidden whenever the wallpaper maps after it."""
    return {1: "TOP", 2: "OVERLAY"}.get(z, "BOTTOM")
