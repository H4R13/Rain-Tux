"""``Meter=Bar``: a bar filled by a measure's relative value (colour or ``BarImage``)."""

from __future__ import annotations

from typing import Any

from raintux.meters.base_meter import Meter
from raintux.meters.image_meter import image_options
from raintux.meters.images import Image, draw_image_scaled, load_image


class BarMeter(Meter):
    def read_meter(self) -> None:
        self.bar_color = self.get_color("barcolor", (0, 0, 0, 1))
        self.vertical = self.get("barorientation", "vertical").strip().lower().startswith("v")
        self.flip = self.get_bool("flip")
        self.border = max(0, self.get_int("barborder", 0))
        name = self.get("barimage")
        self.bar_image: Image | None = load_image(self.skin.path(name), **image_options(self)) if name else None

    def update_meter(self) -> None:
        if self.bar_image is not None:
            self.natural_size(self.bar_image.width, self.bar_image.height)
        else:
            self.natural_size(0, 0)

    def value(self) -> float:
        m = self.measure_value(0)
        return m.relative() if m is not None else 0.0

    def draw_content(self, cr: Any) -> None:
        w, h = self.content_w, self.content_h
        if w <= 0 or h <= 0:
            return
        v = self.value()
        b = self.border
        if self.vertical:
            size = max(0.0, h - 2 * b) * v + 2 * b
            size = min(size, h)
            rect = (0.0, 0.0, w, size) if self.flip else (0.0, h - size, w, size)
        else:
            size = max(0.0, w - 2 * b) * v + 2 * b
            size = min(size, w)
            rect = (w - size, 0.0, size, h) if self.flip else (0.0, 0.0, size, h)
        x, y, rw, rh = rect
        if rw <= 0 or rh <= 0:
            return
        if self.bar_image is not None:
            iw, ih = self.bar_image.width, self.bar_image.height
            sx, sy = x * iw / w, y * ih / h
            draw_image_scaled(cr, self.bar_image, x, y, rw, rh, src=(sx, sy, rw * iw / w, rh * ih / h))
        else:
            cr.set_source_rgba(*self.bar_color)
            cr.rectangle(x, y, rw, rh)
            cr.fill()
