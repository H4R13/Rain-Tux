"""``Meter=Histogram`` and ``Meter=Line``: scrolling graphs of measure history."""

from __future__ import annotations

from collections import deque
from typing import Any

from raintux.meters.base_meter import Meter


def _graph_options(meter: Meter) -> tuple[bool, bool, bool]:
    """(right_to_left, horizontal_orientation, flip)."""
    start = meter.get("graphstart", "right").strip().lower()
    orient = meter.get("graphorientation", "vertical").strip().lower()
    return (start != "left", orient.startswith("h"), meter.get_bool("flip"))


class _Graph(Meter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.history: list[deque[float]] = []

    def samples(self) -> int:
        _rtl, horizontal, _flip = _graph_options(self)
        n = int(self.content_h if horizontal else self.content_w)
        return max(1, n)

    def _push(self, values: list[float]) -> None:
        n = self.samples()
        if any(d.maxlen != n for d in self.history):  # the meter was resized
            self.history = [deque(d, maxlen=n) for d in self.history]
        while len(self.history) < len(values):
            self.history.append(deque(maxlen=n))
        for dq, v in zip(self.history, values):
            dq.append(v)

    def _xy(self, i: int, count: int, frac: float) -> tuple[float, float]:
        """Map sample i (0 = newest) and value fraction to content coordinates."""
        rtl, horizontal, flip = _graph_options(self)
        w, h = self.content_w, self.content_h
        if horizontal:
            pos = (h - 1 - i) if rtl else i
            val = frac * w
            return ((w - val) if flip else val, pos + 0.5)
        pos = (w - 1 - i) if rtl else i
        val = frac * h
        return (pos + 0.5, val if flip else (h - val))


class HistogramMeter(_Graph):
    def read_meter(self) -> None:
        self.primary = self.get_color("primarycolor", (0, 0.5, 0, 1))
        self.secondary = self.get_color("secondarycolor", (0.5, 0, 0, 1))
        self.both = self.get_color("bothcolor", (0.5, 0.5, 0, 1))
        self.auto_scale = self.get_bool("autoscale")

    def update_meter(self) -> None:
        self.natural_size(0, 0)
        vals = []
        for m in self.measures[:2]:
            vals.append(m.value if self.auto_scale else m.relative())
        if vals:
            self._push(vals)

    def draw_content(self, cr: Any) -> None:
        if not self.history:
            return
        _rtl, horizontal, _flip = _graph_options(self)
        peak = 1.0
        if self.auto_scale:
            peak = max((max(d) for d in self.history if d), default=1.0) or 1.0
        prim = list(self.history[0])
        sec = list(self.history[1]) if len(self.history) > 1 else None
        n = len(prim)
        for i in range(n):
            a = prim[n - 1 - i] / peak
            b = sec[n - 1 - i] / peak if sec is not None and i < len(sec) else None
            parts: list[tuple[float, float, tuple[float, float, float, float]]] = []
            if b is None:
                parts.append((0.0, a, self.primary))
            else:
                lo, hi = min(a, b), max(a, b)
                parts.append((0.0, lo, self.both))
                parts.append((lo, hi, self.primary if a > b else self.secondary))
            for f0, f1, col in parts:
                if f1 <= f0:
                    continue
                cr.set_source_rgba(*col)
                x0, y0 = self._xy(i, n, max(0.0, min(1.0, f0)))
                x1, y1 = self._xy(i, n, max(0.0, min(1.0, f1)))
                if horizontal:
                    cr.rectangle(min(x0, x1), y0 - 0.5, abs(x1 - x0), 1)
                else:
                    cr.rectangle(x0 - 0.5, min(y0, y1), 1, abs(y1 - y0))
                cr.fill()


class LineMeter(_Graph):
    def read_meter(self) -> None:
        self.count = max(1, self.get_int("linecount", 1))
        self.colors = []
        self.scales = []
        for i in range(1, self.count + 1):
            sfx = "" if i == 1 else str(i)
            self.colors.append(self.get_color("linecolor" + sfx, (1, 1, 1, 1)))
            self.scales.append(self.get_number("scale" + sfx, 1.0) or 1.0)
        self.line_width = self.get_number("linewidth", 1.0)
        self.horizontal_lines = self.get_bool("horizontallines")
        self.horizontal_color = self.get_color("horizontalcolor", (0, 0, 0, 1))
        self.auto_scale = self.get_bool("autoscale")

    def update_meter(self) -> None:
        self.natural_size(0, 0)
        vals = []
        for i, m in enumerate(self.measures[: self.count]):
            vals.append(m.value * self.scales[i] if self.auto_scale else min(1.0, m.relative() * self.scales[i]))
        if vals:
            self._push(vals)

    def draw_content(self, cr: Any) -> None:
        w, h = self.content_w, self.content_h
        if self.horizontal_lines:
            cr.set_source_rgba(*self.horizontal_color)
            cr.set_line_width(1)
            for k in range(1, 4):
                y = round(h * k / 4) + 0.5
                cr.move_to(0, y)
                cr.line_to(w, y)
            cr.stroke()
        peak = 1.0
        if self.auto_scale:
            peak = max((max(d) for d in self.history if d), default=1.0) or 1.0
        cr.set_line_width(max(0.1, self.line_width))
        for li, dq in enumerate(self.history):
            pts = list(dq)
            n = len(pts)
            if n < 2:
                continue
            cr.set_source_rgba(*self.colors[li] if li < len(self.colors) else (1, 1, 1, 1))
            for i in range(n):
                x, y = self._xy(i, n, max(0.0, min(1.0, pts[n - 1 - i] / peak)))
                if i == 0:
                    cr.move_to(x, y)
                else:
                    cr.line_to(x, y)
            cr.stroke()
