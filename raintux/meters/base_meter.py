"""Meter base class: position, size, padding, background, mouse actions, containers."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

from raintux.core.formula import parse_number
from raintux.core.section import Section, parse_color

if TYPE_CHECKING:
    from raintux.core.skin import Skin
    from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)

Color = tuple[float, float, float, float]

CLICK_ACTIONS = tuple(
    f"{b}mouse{e}action" for b in ("left", "right", "middle", "x1", "x2") for e in ("up", "down", "doubleclick")
) + ("mousescrollupaction", "mousescrolldownaction", "mousescrollleftaction", "mousescrollrightaction")
HOVER_ACTIONS = ("mouseoveraction", "mouseleaveaction")


def parse_coord(value: str, prev_pos: float, prev_end: float, fallback: float = 0, resolve: Any = None) -> float:
    """
    Parse Rainmeter ``X`` / ``Y``.

    ``…R`` (or ``…B`` for Y) is relative to the previous meter's right / bottom
    edge (``prev_end``); lower-case ``…r`` (or ``…b``) is relative to the previous
    meter's own X / Y (``prev_pos``). Formulas in parentheses are evaluated;
    unparseable values return ``fallback``.
    """
    value = value.strip()
    base = 0.0
    if value.endswith(("R", "B")):
        base, value = prev_end, value[:-1]
    elif value.endswith(("r", "b")):
        base, value = prev_pos, value[:-1]
    if not value:
        return base
    marker = object()
    n = parse_number(value, marker, resolve)  # type: ignore[arg-type]
    if n is marker:
        log.debug("unparseable coordinate %r", value)
        return fallback
    return base + n


def parse_numbers(value: str, count: int, default: float = 0.0, resolve: Any = None) -> list[float]:
    """``"1,2,3"`` → floats, padded/truncated to ``count`` (formulas allowed per item)."""
    parts = split_top_level(value, ",") if value.strip() else []
    out = [parse_number(p, default, resolve) for p in parts[:count]]
    return out + [default] * (count - len(out))


def split_top_level(text: str, sep: str) -> list[str]:
    """Split on ``sep`` outside parentheses (``(1,2),3`` keeps the formula together)."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return out


def linear_gradient(cr: Any, x: float, y: float, w: float, h: float, angle_deg: float, stops: list[tuple[float, Color]]) -> Any:
    """Cairo linear gradient covering a box at ``angle_deg`` (0 = left→right, clockwise)."""
    import cairo

    a = math.radians(angle_deg)
    cx, cy = x + w / 2, y + h / 2
    dx, dy = math.cos(a), math.sin(a)
    half = abs(w / 2 * dx) + abs(h / 2 * dy)
    pat = cairo.LinearGradient(cx - dx * half, cy - dy * half, cx + dx * half, cy + dy * half)
    for off, col in stops:
        pat.add_color_stop_rgba(off, *col)
    return pat


def draw_box_fill(cr: Any, x: float, y: float, w: float, h: float, c1: Color, c2: Color | None, angle: float) -> None:
    if w <= 0 or h <= 0:
        return
    if c2 is None or c2 == c1:
        if c1[3] <= 0:
            return
        cr.set_source_rgba(*c1)
    else:
        cr.set_source(linear_gradient(cr, x, y, w, h, angle, [(0.0, c1), (1.0, c2)]))
    cr.rectangle(x, y, w, h)
    cr.fill()


def draw_bevel(cr: Any, x: float, y: float, w: float, h: float, bevel: int) -> None:
    """``BevelType=1`` raised / ``2`` sunken: 1px light and dark edges."""
    if bevel not in (1, 2) or w < 2 or h < 2:
        return
    light, dark = (1, 1, 1, 1), (0, 0, 0, 1)
    tl, br = (light, dark) if bevel == 1 else (dark, light)
    cr.set_line_width(1)
    cr.set_source_rgba(*tl)
    cr.move_to(x + 0.5, y + h - 0.5)
    cr.line_to(x + 0.5, y + 0.5)
    cr.line_to(x + w - 0.5, y + 0.5)
    cr.stroke()
    cr.set_source_rgba(*br)
    cr.move_to(x + w - 0.5, y + 0.5)
    cr.line_to(x + w - 0.5, y + h - 0.5)
    cr.line_to(x + 0.5, y + h - 0.5)
    cr.stroke()


def draw_skin_background(skin: Skin, cr: Any) -> None:
    """``[Rainmeter]`` Background / BackgroundMode / SolidColor / BevelType."""
    rm = skin.rm
    bg = rm.get("background")
    mode = rm.get_int("backgroundmode", 0 if bg else 1)
    w, h = skin.width, skin.height
    if mode == 2:
        c1 = rm.get_color("solidcolor", (128 / 255, 128 / 255, 128 / 255, 1.0))
        c2 = rm.get_color("solidcolor2", c1) if rm.has("solidcolor2") else None
        draw_box_fill(cr, 0, 0, w, h, c1, c2, rm.get_number("gradientangle", 0))
    elif bg and mode in (0, 3, 4):
        from raintux.meters.images import draw_image_scaled, load_image

        img = load_image(skin.path(bg))
        if img is not None:
            if mode == 0:
                draw_image_scaled(cr, img, 0, 0, img.width, img.height)
            elif mode == 3:
                margins = parse_numbers(rm.get("backgroundmargins"), 4)
                draw_image_scaled(cr, img, 0, 0, w, h, margins=margins if any(margins) else None)
            else:
                draw_image_scaled(cr, img, 0, 0, w, h, tile=True)
    draw_bevel(cr, 0, 0, w, h, rm.get_int("beveltype", 0))


class Meter(Section):
    """Shared meter behaviour. Subclasses implement :meth:`update_meter` and :meth:`draw_content`."""

    kind = "meter"

    def __init__(self, skin: Skin, name: str, raw: dict[str, str]) -> None:
        super().__init__(skin, name, raw)
        self.relative_to: Meter | None = None
        self.x = 0.0  # anchor position (X=/Y= after relative positioning)
        self.y = 0.0
        self.content_w = 0.0  # natural or explicit size, without padding
        self.content_h = 0.0
        self.padding = (0.0, 0.0, 0.0, 0.0)
        self.hidden = False
        self.container_name = ""
        self.children: list[Meter] = []
        self.measures: list[Measure] = []
        self.matrix: tuple[float, ...] | None = None
        self.x_spec = "0"
        self.y_spec = "0"
        self.w_opt: float | None = None
        self.h_opt: float | None = None
        self._hidden_spec: str | None = None
        self._style_cache: tuple[str, list[dict[str, str]]] | None = None
        self.disabled_actions: set[str] = set()  # !DisableMouseAction
        self.cleared_actions: set[str] = set()  # !ClearMouseAction (until refresh)

    # -- options ---------------------------------------------------------------
    def styles(self) -> list[dict[str, str]]:
        spec = self.raw.get("meterstyle", "")
        if "meterstyle" in self.overrides:
            spec = self.overrides["meterstyle"]
        if not spec:
            return []
        if self._style_cache and self._style_cache[0] == spec:
            return self._style_cache[1]
        names = [n.strip() for n in self.skin.resolve(spec).split("|") if n.strip()]
        found = []
        for n in reversed(names):  # later styles win
            st = self.skin.style(n)
            if st is not None:
                found.append(st)
            else:
                log.debug("[%s] MeterStyle %r not found", self.name, n)
        self._style_cache = (spec, found)
        return found

    def read_options(self) -> None:
        self.x_spec = self.get("x", "0") or "0"
        self.y_spec = self.get("y", "0") or "0"
        w = self.get("w").strip()
        h = self.get("h").strip()
        self.w_opt = parse_number(w, 0.0, self.skin.measure_number) if w else None
        self.h_opt = parse_number(h, 0.0, self.skin.measure_number) if h else None
        p = parse_numbers(self.get("padding"), 4, 0.0, self.skin.measure_number)
        self.padding = (p[0], p[1], p[2], p[3])
        # Re-read on every read like Rainmeter; !ShowMeter / !HideMeter also set the option.
        self.hidden = parse_number(self.get("hidden", "0"), 0.0, self.skin.measure_number) != 0
        self.solid = self.get_color("solidcolor", (0, 0, 0, 0))
        self.solid2 = self.get_color("solidcolor2", self.solid) if self.has("solidcolor2") else None
        self.gradient_angle = self.get_number("gradientangle", 0)
        self.bevel = self.get_int("beveltype", 0)
        self.antialias = self.get_bool("antialias", True)
        self.container_name = self.get("container").strip()
        m = [v for v in self.get("transformationmatrix").replace(",", ";").split(";") if v.strip()]
        self.matrix = tuple(parse_number(v, 0.0) for v in m) if len(m) == 6 else None
        self.measures = []
        i = 1
        while True:
            key = "measurename" if i == 1 else f"measurename{i}"
            if not self.has(key):
                break
            ms = self.skin.measure(self.get(key))
            if ms is None:
                log.debug("[%s] %s=%r not found", self.name, key, self.get(key))
            else:
                self.measures.append(ms)
            i += 1
        self.read_meter()

    def default_update_divider(self) -> int:
        return self.skin.default_update_divider

    def set_hidden(self, hidden: bool) -> None:
        """``!ShowMeter`` / ``!HideMeter``: Rainmeter changes the Hidden option too."""
        self.hidden = hidden
        self.overrides["hidden"] = "1" if hidden else "0"

    def read_meter(self) -> None:
        """Type-specific options."""

    # -- update / layout -------------------------------------------------------
    def update(self, force: bool = False) -> bool:
        if self.needs_read or self.dynamic:
            self.read()
        if not force and not self.due():
            return False
        try:
            self.update_meter()
        except Exception:
            log.exception("meter [%s] update failed", self.name)
        return True

    def update_meter(self) -> None:
        """Compute content and natural size (``content_w`` / ``content_h``)."""
        self.content_w = self.w_opt or 0.0
        self.content_h = self.h_opt or 0.0

    def natural_size(self, w: float, h: float) -> None:
        """Use explicit W/H when given, otherwise the content's natural size."""
        self.content_w = self.w_opt if self.w_opt is not None else w
        self.content_h = self.h_opt if self.h_opt is not None else h

    @property
    def w(self) -> float:
        return self.content_w + self.padding[0] + self.padding[2]

    @property
    def h(self) -> float:
        return self.content_h + self.padding[1] + self.padding[3]

    def box(self) -> tuple[float, float, float, float]:
        """Meter rectangle (x, y, w, h) including padding; strings shift it for alignment."""
        return (self.x, self.y, self.w, self.h)

    def visible_size(self) -> tuple[float, float]:
        """Rainmeter's GetW()/GetH(): 0 while hidden (relative positions and window size use it)."""
        return (0.0, 0.0) if self.hidden else (self.w, self.h)

    def layout(self) -> None:
        """Rainmeter positioning: ``r`` = previous meter's X/Y, ``R`` = its X/Y plus its W/H
        (anchor positions, so right-aligned text measures from its anchor); absolute positions of
        contained meters are offset by the container."""
        prev = self.relative_to
        if prev is not None:
            ax, ay = prev.x, prev.y
            pw, ph = prev.visible_size()
        else:
            ax = ay = pw = ph = 0.0
        res = self.skin.measure_number
        self.x = parse_coord(self.x_spec, ax, ax + pw, 0, res)
        self.y = parse_coord(self.y_spec, ay, ay + ph, 0, res)
        if self.container_name:
            cont = self.skin.meter(self.container_name)
            if cont is not None and cont is not self:
                if not self.x_spec.strip().endswith(("r", "R")):
                    self.x += cont.x
                if not self.y_spec.strip().endswith(("r", "R", "b", "B")):
                    self.y += cont.y

    # -- mouse -----------------------------------------------------------------
    def mouse_action(self, name: str) -> str:
        n = name.lower()
        blocked = self.disabled_actions | self.cleared_actions
        if n in blocked or "*" in blocked:
            return ""
        return self.get(n)

    def has_mouse_actions(self) -> bool:
        return any(self.mouse_action(k) for k in CLICK_ACTIONS)

    def hit_test(self, x: float, y: float) -> bool:
        if self.matrix is not None:
            x, y = _invert_point(self.matrix, x, y)
        bx, by, bw, bh = self.box()
        if not (bx <= x < bx + bw and by <= y < by + bh):
            return False
        if self.container_name:
            cont = self.skin.meter(self.container_name)
            if cont is not None and cont is not self:
                cx, cy, cw, ch = cont.box()
                if not (cx <= x < cx + cw and cy <= y < cy + ch):
                    return False
        return self.hit_content(x - bx - self.padding[0], y - by - self.padding[1])

    def hit_content(self, x: float, y: float) -> bool:
        """Point relative to the content area; image meters check transparency."""
        return True

    # -- drawing ----------------------------------------------------------------
    def draw(self, cr: Any) -> None:
        if self.hidden:
            return
        if self.children:
            self._draw_container(cr)
            return
        self._draw_self(cr)

    def _draw_self(self, cr: Any) -> None:
        import cairo

        cr.save()
        try:
            if self.matrix is not None:
                cr.transform(cairo.Matrix(*self.matrix))
            if not self.antialias:
                cr.set_antialias(cairo.ANTIALIAS_NONE)
            bx, by, bw, bh = self.box()
            draw_box_fill(cr, bx, by, bw, bh, self.solid, self.solid2, self.gradient_angle)
            cr.save()
            cr.translate(bx + self.padding[0], by + self.padding[1])
            self.draw_content(cr)
            cr.restore()
            draw_bevel(cr, bx, by, bw, bh, self.bevel)
        except Exception:
            log.exception("meter [%s] draw failed", self.name)
        finally:
            cr.restore()

    def _draw_container(self, cr: Any) -> None:
        """Children are drawn masked by this meter's own pixels (Rainmeter ``Container=``)."""
        cr.push_group()
        for child in self.children:
            if not child.hidden:
                child.draw(cr)
        content = cr.pop_group()
        cr.push_group()
        self._draw_self(cr)
        mask = cr.pop_group()
        cr.save()
        cr.set_source(content)
        cr.mask(mask)
        cr.restore()

    def draw_content(self, cr: Any) -> None:
        """Paint at (0, 0) within the content area (padding already applied)."""

    # -- helpers -----------------------------------------------------------------
    def measure_value(self, index: int = 0) -> Measure | None:
        return self.measures[index] if index < len(self.measures) else None

    def color(self, key: str, default: Color) -> Color:
        return parse_color(self.get(key), default) if self.has(key) else default


def _invert_point(m: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if det == 0:
        return x, y
    x -= e
    y -= f
    return ((d * x - c * y) / det, (-b * x + a * y) / det)
