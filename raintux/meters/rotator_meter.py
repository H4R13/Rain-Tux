"""``Meter=Rotator``: an image rotated by a measure value (clock hands, gauges)."""

from __future__ import annotations

import math
from typing import Any

from raintux.meters.base_meter import Meter
from raintux.meters.image_meter import image_options
from raintux.meters.images import Image, draw_image_scaled, load_image


def value_fraction(meter: Meter, remainder: int) -> float:
    m = meter.measure_value(0)
    if m is None:
        return 0.0
    if remainder > 0:
        return (int(m.value) % remainder) / float(remainder)
    return m.relative()


class RotatorMeter(Meter):
    def read_meter(self) -> None:
        name = self.get("imagename")
        self.img: Image | None = load_image(self.skin.path(name), **image_options(self)) if name else None
        self.offset_x = self.get_number("offsetx", 0)
        self.offset_y = self.get_number("offsety", 0)
        self.start_angle = self.get_number("startangle", 0)
        self.rotation = self.get_number("rotationangle", 2 * math.pi)
        self.remainder = self.get_int("valueremainder", 0)
        self.alpha = max(0.0, min(255.0, self.get_number("imagealpha", 255))) / 255.0

    def update_meter(self) -> None:
        if self.img is None:
            self.natural_size(0, 0)
            return
        self.natural_size(self.img.width, self.img.height)

    def draw_content(self, cr: Any) -> None:
        if self.img is None:
            return
        angle = self.start_angle + self.rotation * value_fraction(self, self.remainder)
        cr.save()
        cr.translate(self.content_w / 2, self.content_h / 2)
        cr.rotate(angle)
        draw_image_scaled(cr, self.img, -self.offset_x, -self.offset_y, self.img.width, self.img.height, alpha=self.alpha)
        cr.restore()
