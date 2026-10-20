"""``Meter=Roundline``: a needle or a filled ring segment driven by a measure."""

from __future__ import annotations

import math
from typing import Any

from raintux.meters.base_meter import Meter
from raintux.meters.rotator_meter import value_fraction


class RoundlineMeter(Meter):
    def read_meter(self) -> None:
        self.start_angle = self.get_number("startangle", 0)
        self.rotation = self.get_number("rotationangle", 2 * math.pi)
        self.line_start = self.get_number("linestart", 0)
        self.line_length = self.get_number("linelength", -1) if self.has("linelength") else None
        self.line_width = self.get_number("linewidth", 1)
        self.line_color = self.get_color("linecolor", (0, 0, 0, 1))
        self.solid_fill = self.get_bool("solid")
        self.start_shift = self.get_number("linestartshift", 0)
        self.length_shift = self.get_number("linelengthshift", 0)
        self.control_angle = self.get_bool("controlangle", True)
        self.control_start = self.get_bool("controlstart")
        self.control_length = self.get_bool("controllength")
        self.remainder = self.get_int("valueremainder", 0)

    def update_meter(self) -> None:
        self.natural_size(0, 0)

    def draw_content(self, cr: Any) -> None:
        w, h = self.content_w, self.content_h
        cx, cy = w / 2.0, h / 2.0
        # Without a MeasureName Rainmeter draws a Roundline full (value 1), e.g. a background ring.
        v = value_fraction(self, self.remainder) if self.measures else 1.0
        outer = self.line_length if self.line_length is not None else min(w, h) / 2.0
        inner = self.line_start
        if self.control_length:
            outer += self.length_shift * v
        if self.control_start:
            inner += self.start_shift * v
        cr.set_source_rgba(*self.line_color)
        if self.solid_fill:
            sweep = self.rotation * (v if self.control_angle else 1.0)
            if sweep == 0:
                return
            a0, a1 = self.start_angle, self.start_angle + sweep
            arc_out, arc_in = (cr.arc, cr.arc_negative) if sweep > 0 else (cr.arc_negative, cr.arc)
            cr.new_path()
            arc_out(cx, cy, max(0.0, outer), a0, a1)
            if inner > 0:
                arc_in(cx, cy, inner, a1, a0)
            else:
                cr.line_to(cx, cy)
            cr.close_path()
            cr.fill()
        else:
            a = self.start_angle + self.rotation * (v if self.control_angle else 1.0)
            cr.set_line_width(max(0.1, self.line_width))
            cr.move_to(cx + math.cos(a) * inner, cy + math.sin(a) * inner)
            cr.line_to(cx + math.cos(a) * outer, cy + math.sin(a) * outer)
            cr.stroke()
