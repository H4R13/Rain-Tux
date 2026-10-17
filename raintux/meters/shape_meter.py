"""``Meter=Shape``: Rainmeter's vector shape syntax (Direct2D) rendered with Cairo.

Supported: Rectangle (rounded), Ellipse, Line, Arc, Curve, Path (LineTo / ArcTo /
CurveTo / ClosePath), Combine (Union / Intersect / XOR / Exclude); modifiers Fill
and Stroke (Color, LinearGradient, RadialGradient), StrokeWidth, caps, joins, dashes,
Rotate / Scale / Skew / Offset with TransformOrder, and Extend.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from raintux.core.formula import parse_number
from raintux.core.section import parse_color
from raintux.meters.base_meter import Meter, split_top_level

log = logging.getLogger(__name__)

Paint = tuple[Any, ...]  # ("color", rgba) | ("linear", angle, stops) | ("radial", spec, stops)


@dataclass
class ShapeDef:
    key: str
    kind: str
    args: list[str]
    fill: Paint | None = ("color", (1.0, 1.0, 1.0, 1.0))
    stroke: Paint | None = ("color", (0.0, 0.0, 0.0, 1.0))
    stroke_width: float = 1.0
    start_cap: str = "flat"
    end_cap: str = "flat"
    join: str = "miter"
    miter_limit: float = 10.0
    dashes: list[float] = field(default_factory=list)
    dash_offset: float = 0.0
    transforms: dict[str, list[float]] = field(default_factory=dict)
    order: list[str] = field(default_factory=lambda: ["rotate", "scale", "skew", "offset"])
    combine: list[tuple[str, str]] = field(default_factory=list)


def _shape_key(name: str) -> str:
    """Option key of a Combine reference: ``Shape`` and ``Shape1`` are both the first shape."""
    key = name.strip().lower()
    return "shape" if key == "shape1" else key


def _nums(text: str, meter: Meter) -> list[float]:
    if not text.strip():
        return []
    return [parse_number(p, 0.0, meter.skin.measure_number) for p in split_top_level(text, ",")]


def _stops(parts: list[str]) -> list[tuple[float, tuple[float, float, float, float]]]:
    stops = []
    for p in parts:
        color, _sep, off = p.partition(";")
        stops.append((parse_number(off.strip(), 0.0) if off.strip() else 0.0, parse_color(color, (0, 0, 0, 1))))
    stops.sort(key=lambda s: s[0])
    return stops


class ShapeMeter(Meter):
    def read_meter(self) -> None:
        self.shapes: list[ShapeDef] = []
        i = 1
        while True:
            key = "shape" if i == 1 else f"shape{i}"
            spec = self.opts.get(key)
            if spec is None:
                break
            sd = self._parse(key, spec)
            if sd is not None:
                self.shapes.append(sd)
            i += 1
        # Shapes used by a Combine (its base and operands) are only drawn through it.
        used = {k for sd in self.shapes for _op, k in sd.combine}
        used |= {_shape_key(sd.args[0]) for sd in self.shapes if sd.kind == "combine" and sd.args}
        self.by_key = {sd.key: sd for sd in self.shapes}
        self.drawn = [sd for sd in self.shapes if sd.key not in used]
        self.bounds = (0.0, 0.0, 0.0, 0.0)

    # -- parsing ---------------------------------------------------------------
    def _parse(self, key: str, spec: str) -> ShapeDef | None:
        parts = split_top_level(spec, "|")
        head = parts[0].strip()
        kind, _sep, rest = head.partition(" ")
        kind = kind.strip().lower()
        if kind not in ("rectangle", "ellipse", "line", "arc", "curve", "path", "combine"):
            log.warning("[%s] unknown shape %r", self.name, kind)
            return None
        sd = ShapeDef(key=key, kind=kind, args=[a for a in split_top_level(rest, ",")] if kind != "path" and kind != "combine" else [rest.strip()])
        if kind in ("arc", "curve"):
            # Open arcs and curves get a transparent fill unless a Fill is given (MeterShape.cpp).
            vals = _nums(rest, self)
            closed = (len(vals) > 9 and vals[9] == 0) if kind == "arc" else (
                (len(vals) == 7 and vals[6] != 0) or (len(vals) >= 9 and vals[8] != 0))
            if not closed:
                sd.fill = ("color", (0.0, 0.0, 0.0, 0.0))
        if kind == "combine":
            mods = []
            for p in parts[1:]:
                word, _s, arg = p.strip().partition(" ")
                if word.lower() in ("union", "intersect", "xor", "exclude"):
                    sd.combine.append((word.lower(), _shape_key(arg)))
                else:
                    mods.append(p)
            self._modifiers(sd, mods)
        else:
            self._modifiers(sd, parts[1:])
        return sd

    def _modifiers(self, sd: ShapeDef, mods: list[str], depth: int = 0) -> None:
        for raw in mods:
            m = raw.strip()
            if not m:
                continue
            word, _s, arg = m.partition(" ")
            w = word.lower()
            arg = arg.strip()
            if w in ("fill", "stroke"):
                sub, _s2, val = arg.partition(" ")
                sub_l = sub.lower()
                if sub_l == "color":
                    paint: Paint | None = ("color", parse_color(val, (0, 0, 0, 0)))
                elif sub_l.startswith("lineargradient"):
                    g = self.get(val.strip())
                    gp = split_top_level(g, "|")
                    paint = ("linear", parse_number(gp[0], 0.0) if gp else 0.0, _stops(gp[1:]))
                elif sub_l.startswith("radialgradient"):
                    g = self.get(val.strip())
                    gp = split_top_level(g, "|")
                    spec = [_nums(gp[i], self) if i < len(gp) else [] for i in range(3)]
                    paint = ("radial", spec, _stops(gp[3:]))
                else:
                    paint = None
                if w == "fill":
                    sd.fill = paint
                else:
                    sd.stroke = paint
            elif w == "strokewidth":
                sd.stroke_width = parse_number(arg, 1.0, self.skin.measure_number)
            elif w in ("strokestartcap", "strokeendcap", "strokedashcap"):
                if w == "strokestartcap":
                    sd.start_cap = arg.lower()
                elif w == "strokeendcap":
                    sd.end_cap = arg.lower()
            elif w == "strokelinejoin":
                j, _c, lim = arg.partition(",")
                sd.join = j.strip().lower()
                if lim.strip():
                    sd.miter_limit = parse_number(lim, 10.0)
            elif w == "strokedashes":
                sd.dashes = [v for v in _nums(arg, self) if v >= 0]
            elif w == "strokedashoffset":
                sd.dash_offset = parse_number(arg, 0.0)
            elif w in ("rotate", "scale", "skew", "offset"):
                sd.transforms[w] = _nums(arg, self)
            elif w == "transformorder":
                order = [o.strip().lower() for o in arg.split(",") if o.strip()]
                if order:
                    sd.order = order
            elif w == "extend" and depth < 8:
                for name in arg.split(","):
                    extra = self.get(name.strip())
                    if extra:
                        self._modifiers(sd, split_top_level(extra, "|"), depth + 1)
            else:
                log.debug("[%s] unsupported shape modifier %r", self.name, m)

    # -- geometry --------------------------------------------------------------
    def _path(self, cr: Any, sd: ShapeDef) -> None:
        """Append the untransformed path of ``sd`` to ``cr``."""
        k = sd.kind
        a = [parse_number(x, 0.0, self.skin.measure_number) for x in sd.args] if k not in ("path", "combine") else []
        if k == "rectangle":
            x, y, w, h = (a + [0, 0, 0, 0])[:4]
            rx = a[4] if len(a) > 4 else 0.0
            ry = a[5] if len(a) > 5 else rx
            _rounded_rect(cr, x, y, w, h, rx, ry)
        elif k == "ellipse":
            cx, cy, rx = (a + [0, 0, 0])[:3]
            ry = a[3] if len(a) > 3 else rx
            if rx > 0 and ry > 0:
                cr.save()
                cr.translate(cx, cy)
                cr.scale(rx, ry)
                cr.new_sub_path()
                cr.arc(0, 0, 1, 0, 2 * math.pi)
                cr.close_path()
                cr.restore()
        elif k == "line":
            x1, y1, x2, y2 = (a + [0, 0, 0, 0])[:4]
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
        elif k == "arc":
            # Rainmeter: Arc x1,y1,x2,y2,[RadiusX],[RadiusY],[Angle],[Sweep],[Size],[Open]; the
            # flags are 0 = clockwise / small / open (the defaults) and "*" keeps a default.
            given = [not str(v).strip().startswith("*") for v in sd.args] + [False] * 10
            x1, y1, x2, y2 = (a + [0, 0, 0, 0])[:4]
            rx = a[4] if len(a) > 4 and given[4] else math.hypot(x2 - x1, y2 - y1) / 2
            ry = a[5] if len(a) > 5 and given[5] else rx
            angle = a[6] if len(a) > 6 and given[6] else 0.0
            clockwise = not (len(a) > 7 and given[7] and int(a[7]) != 0)
            small = not (len(a) > 8 and given[8] and int(a[8]) != 0)
            closed = len(a) > 9 and given[9] and int(a[9]) != 0
            cr.move_to(x1, y1)
            _svg_arc(cr, x1, y1, rx, ry, angle, 0 if small else 1, clockwise, x2, y2)
            if closed:
                cr.close_path()
        elif k == "curve":
            x1, y1, x2, y2, c1x, c1y = (a + [0] * 6)[:6]
            cr.move_to(x1, y1)
            if len(a) >= 8:
                cr.curve_to(c1x, c1y, a[6], a[7], x2, y2)
            else:
                _quad_to(cr, x1, y1, c1x, c1y, x2, y2)
            if (len(a) == 7 and int(a[6])) or (len(a) >= 9 and int(a[8])):
                cr.close_path()
        elif k == "path":
            self._custom_path(cr, sd.args[0].strip())

    def _custom_path(self, cr: Any, option: str) -> None:
        spec = self.get(option)
        if not spec:
            log.debug("[%s] path option %r is empty", self.name, option)
            return
        segs = split_top_level(spec, "|")
        start = _nums(segs[0], self)
        if len(start) < 2:
            return
        x, y = start[0], start[1]
        cr.move_to(x, y)
        for seg in segs[1:]:
            word, _s, arg = seg.strip().partition(" ")
            w = word.lower()
            v = _nums(arg, self)
            if w == "lineto" and len(v) >= 2:
                cr.line_to(v[0], v[1])
                x, y = v[0], v[1]
            elif w == "arcto" and len(v) >= 2:
                ex, ey = v[0], v[1]
                rx = v[2] if len(v) > 2 else math.hypot(ex - x, ey - y) / 2
                ry = v[3] if len(v) > 3 else rx
                angle = v[4] if len(v) > 4 else 0.0
                clockwise = not (len(v) > 5 and int(v[5]) != 0)  # 0 = clockwise (default)
                small = not (len(v) > 6 and int(v[6]) != 0)  # 0 = small arc (default)
                _svg_arc(cr, x, y, rx, ry, angle, 0 if small else 1, clockwise, ex, ey)
                x, y = ex, ey
            elif w == "curveto" and len(v) >= 4:
                if len(v) >= 6:
                    cr.curve_to(v[2], v[3], v[4], v[5], v[0], v[1])
                else:
                    _quad_to(cr, x, y, v[2], v[3], v[0], v[1])
                x, y = v[0], v[1]
            elif w == "closepath":
                if not v or v[0]:
                    cr.close_path()
            # SetWinding / SetRoundJoin and other hints are ignored.

    def _transform(self, cr: Any, sd: ShapeDef, scratch: Any) -> None:
        """Apply Rotate/Scale/Skew/Offset (anchors default to the shape's centre)."""
        if not sd.transforms:
            return
        x1, y1, x2, y2 = self._anchor_extents(sd, scratch)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        # Points go through the ops in TransformOrder; Cairo composes in reverse.
        for op in reversed([o for o in sd.order if o in sd.transforms]):
            v = sd.transforms[op]
            if op == "offset":
                cr.translate(v[0] if v else 0, v[1] if len(v) > 1 else 0)
            elif op == "rotate":
                ax = v[1] if len(v) > 1 else cx
                ay = v[2] if len(v) > 2 else cy
                cr.translate(ax, ay)
                cr.rotate(math.radians(v[0] if v else 0))
                cr.translate(-ax, -ay)
            elif op == "scale":
                sx = v[0] if v else 1
                sy = v[1] if len(v) > 1 else sx
                ax = v[2] if len(v) > 2 else cx
                ay = v[3] if len(v) > 3 else cy
                cr.translate(ax, ay)
                cr.scale(sx or 1e-6, sy or 1e-6)
                cr.translate(-ax, -ay)
            elif op == "skew":
                import cairo

                kx = math.tan(math.radians(v[0] if v else 0))
                ky = math.tan(math.radians(v[1] if len(v) > 1 else 0))
                ax = v[2] if len(v) > 2 else cx
                ay = v[3] if len(v) > 3 else cy
                cr.translate(ax, ay)
                cr.transform(cairo.Matrix(1, ky, kx, 1, 0, 0))
                cr.translate(-ax, -ay)

    def _build(self, cr: Any, sd: ShapeDef, scratch: Any) -> Any:
        """Append ``sd``'s transformed path to ``cr``; return the shape→user matrix."""
        base = cr.get_matrix()
        self._transform(cr, sd, scratch)
        m = cr.get_matrix()
        self._path(cr, sd)
        cr.set_matrix(base)
        rel = m.multiply(_inverted(base))
        return rel

    def _anchor_extents(self, sd: ShapeDef, scratch: Any) -> tuple[float, float, float, float]:
        """Bounds that transform anchors refer to: the shape's path, or a Combine's combined parts."""
        if sd.kind == "combine":
            import cairo

            tmp = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
            for part in self._combine_parts(sd):
                self._build(tmp, part, scratch)
            return tmp.fill_extents()
        scratch.save()
        scratch.identity_matrix()
        scratch.new_path()
        self._path(scratch, sd)
        ext = scratch.fill_extents() if sd.kind != "line" else scratch.stroke_extents()
        scratch.new_path()
        scratch.restore()
        return ext

    def _combine_parts(self, sd: ShapeDef) -> list[ShapeDef]:
        """A Combine's base shape followed by its operands (missing ones skipped)."""
        keys = ([_shape_key(sd.args[0])] if sd.args and sd.args[0].strip() else []) + [k for _op, k in sd.combine]
        return [self.by_key[k] for k in keys if k in self.by_key]

    def update_meter(self) -> None:
        try:
            import cairo
        except ImportError:  # pragma: no cover
            self.natural_size(0, 0)
            return
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surf)
        scratch = cairo.Context(surf)
        right = bottom = 0.0
        for sd in self.drawn:
            if sd.kind == "combine":
                cr.new_path()
                cr.save()
                self._transform(cr, sd, scratch)  # the Combine's own Rotate/Scale/Offset
                for part in self._combine_parts(sd):
                    self._build(cr, part, scratch)
                cr.restore()
                x1, y1, x2, y2 = cr.fill_extents()
                right, bottom = max(right, x2), max(bottom, y2)
                continue
            cr.new_path()
            self._build(cr, sd, scratch)
            x1, y1, x2, y2 = cr.fill_extents()
            if sd.kind in ("line", "arc", "curve", "path") or (x2 - x1) * (y2 - y1) == 0:
                sx1, sy1, sx2, sy2 = _stroke_box(cr, max(sd.stroke_width, 1.0))
                x2, y2 = max(x2, sx2), max(y2, sy2)
            if sd.stroke is not None and sd.stroke_width > 0:
                x2 += sd.stroke_width / 2
                y2 += sd.stroke_width / 2
            right = max(right, x2)
            bottom = max(bottom, y2)
        cr.new_path()
        self.natural_size(math.ceil(max(0.0, right)), math.ceil(max(0.0, bottom)))

    # -- drawing ---------------------------------------------------------------
    def _paint(self, cr: Any, paint: Paint, rel: Any, extents: tuple[float, float, float, float]) -> bool:
        import cairo

        kind = paint[0]
        if kind == "color":
            if paint[1][3] <= 0:
                return False
            cr.set_source_rgba(*paint[1])
            return True
        x1, y1, x2, y2 = extents
        if kind == "linear":
            from raintux.meters.base_meter import linear_gradient

            pat = linear_gradient(cr, x1, y1, x2 - x1, y2 - y1, paint[1], paint[2])
        else:
            center, offset, radius = paint[1]
            cx = (x1 + x2) / 2 + (center[0] if center else 0)
            cy = (y1 + y2) / 2 + (center[1] if len(center) > 1 else 0)
            fx = cx + (offset[0] if offset else 0)
            fy = cy + (offset[1] if len(offset) > 1 else 0)
            rx = radius[0] if radius else max(1.0, (x2 - x1) / 2)
            ry = radius[1] if len(radius) > 1 else rx
            pat = cairo.RadialGradient(fx, fy, 0, cx, cy, max(rx, 1e-3))
            if ry and rx != ry:
                m = cairo.Matrix()
                m.translate(cx, cy)
                m.scale(1, rx / ry)
                m.translate(-cx, -cy)
                pat.set_matrix(m)
            for off, col in paint[2]:
                pat.add_color_stop_rgba(off, *col)
        # Gradients live in shape space: user → shape (inverse of rel), then the pattern's own matrix.
        pm = pat.get_matrix()
        pat.set_matrix(_inverted(rel).multiply(pm))
        cr.set_source(pat)
        return True

    def _shape_extents(self, sd: ShapeDef, scratch: Any) -> tuple[float, float, float, float]:
        scratch.save()
        scratch.identity_matrix()
        scratch.new_path()
        self._path(scratch, sd)
        ext = scratch.fill_extents()
        if ext[2] - ext[0] <= 0 or ext[3] - ext[1] <= 0:
            ext = scratch.stroke_extents()
        scratch.new_path()
        scratch.restore()
        return ext

    def _stroke_setup(self, cr: Any, sd: ShapeDef) -> None:
        import cairo

        cr.set_line_width(sd.stroke_width)
        cap = sd.start_cap or sd.end_cap
        cr.set_line_cap({"round": cairo.LINE_CAP_ROUND, "square": cairo.LINE_CAP_SQUARE,
                         "triangle": cairo.LINE_CAP_ROUND}.get(cap, cairo.LINE_CAP_BUTT))
        cr.set_line_join({"bevel": cairo.LINE_JOIN_BEVEL, "round": cairo.LINE_JOIN_ROUND}.get(sd.join, cairo.LINE_JOIN_MITER))
        cr.set_miter_limit(max(1.0, sd.miter_limit))
        if sd.dashes:
            cr.set_dash([max(0.01, d * sd.stroke_width) for d in sd.dashes], sd.dash_offset * sd.stroke_width)

    def draw_content(self, cr: Any) -> None:
        import cairo

        scratch = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        for sd in self.drawn:
            cr.save()
            try:
                if sd.kind == "combine":
                    self._draw_combined(cr, sd, scratch)
                    continue
                cr.new_path()
                rel = self._build(cr, sd, scratch)
                ext = self._shape_extents(sd, scratch)
                if sd.fill is not None and sd.kind not in ("line",) and self._paint(cr, sd.fill, rel, ext):
                    cr.fill_preserve()
                if sd.stroke is not None and sd.stroke_width > 0 and self._paint(cr, sd.stroke, rel, ext):
                    self._stroke_setup(cr, sd)
                    cr.stroke_preserve()
                cr.new_path()
            except Exception:
                log.exception("[%s] shape %s failed", self.name, sd.key)
            finally:
                cr.restore()

    def _draw_combined(self, cr: Any, sd: ShapeDef, scratch: Any) -> None:
        """Rainmeter's Combine: a copy of the base shape (its fill) merged with the operands,
        then the Combine's own transforms. Filled through a mask (Cairo has no path booleans,
        so a combined shape's stroke is not drawn)."""
        import cairo

        base = self.by_key.get(_shape_key(sd.args[0])) if sd.args and sd.args[0].strip() else None
        if base is None:
            log.warning("[%s] Combine: base shape %r not found", self.name, sd.args)
            return
        ops = {"union": cairo.OPERATOR_OVER, "intersect": cairo.OPERATOR_IN, "xor": cairo.OPERATOR_XOR,
               "exclude": cairo.OPERATOR_DEST_OUT}
        cr.push_group()
        cr.save()
        self._transform(cr, sd, scratch)
        cr.set_source_rgba(1, 1, 1, 1)
        cr.new_path()
        self._build(cr, base, scratch)
        cr.fill()
        for op, key in sd.combine:
            other = self.by_key.get(key)
            if other is None:
                continue
            cr.save()
            cr.set_operator(ops[op])
            cr.set_source_rgba(1, 1, 1, 1)
            cr.new_path()
            self._build(cr, other, scratch)
            cr.fill()
            cr.restore()
        cr.restore()
        mask = cr.pop_group()
        ext = self._shape_extents(base, scratch)
        if base.fill is not None and self._paint(cr, base.fill, cairo.Matrix(), ext):
            cr.mask(mask)


def _inverted(m: Any) -> Any:
    import cairo

    inv = cairo.Matrix(*m)
    try:
        inv.invert()
    except cairo.Error:
        return cairo.Matrix()
    return inv


def _stroke_box(cr: Any, width: float) -> tuple[float, float, float, float]:
    cr.save()
    cr.set_line_width(width)
    ext = cr.stroke_extents()
    cr.restore()
    return ext


def _rounded_rect(cr: Any, x: float, y: float, w: float, h: float, rx: float, ry: float) -> None:
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    rx, ry = min(max(rx, 0.0), w / 2), min(max(ry, 0.0), h / 2)
    if rx <= 0 or ry <= 0:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    for cx, cy, a0 in ((x + w - rx, y + ry, -math.pi / 2), (x + w - rx, y + h - ry, 0.0),
                       (x + rx, y + h - ry, math.pi / 2), (x + rx, y + ry, math.pi)):
        cr.save()
        cr.translate(cx, cy)
        cr.scale(rx, ry)
        cr.arc(0, 0, 1, a0, a0 + math.pi / 2)
        cr.restore()
    cr.close_path()


def _quad_to(cr: Any, x0: float, y0: float, cx: float, cy: float, x: float, y: float) -> None:
    cr.curve_to(x0 + 2 / 3 * (cx - x0), y0 + 2 / 3 * (cy - y0), x + 2 / 3 * (cx - x), y + 2 / 3 * (cy - y), x, y)


def _svg_arc(cr: Any, x1: float, y1: float, rx: float, ry: float, angle_deg: float,
             large: int, clockwise: bool, x2: float, y2: float) -> None:
    """SVG/Direct2D endpoint arc → Cairo arcs (``clockwise`` in screen space)."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        cr.line_to(x2, y2)
        return
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(angle_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if bool(large) == clockwise:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def ang(ux: float, uy: float, vx: float, vy: float) -> float:
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if clockwise and dt < 0:
        dt += 2 * math.pi
    elif not clockwise and dt > 0:
        dt -= 2 * math.pi
    cr.save()
    cr.translate(cx, cy)
    cr.rotate(phi)
    cr.scale(rx, ry)
    if dt > 0:
        cr.arc(0, 0, 1, t1, t1 + dt)
    else:
        cr.arc_negative(0, 0, 1, t1, t1 + dt)
    cr.restore()
