"""``Meter=Bitmap``: pick a frame by value, or draw digits with ``BitmapExtend=1``."""

from __future__ import annotations

from typing import Any

from raintux.meters.base_meter import Meter
from raintux.meters.image_meter import image_options
from raintux.meters.images import Image, draw_image_scaled, load_image


class BitmapMeter(Meter):
    def read_meter(self) -> None:
        name = self.get("bitmapimage")
        self.img: Image | None = load_image(self.skin.path(name), **image_options(self)) if name else None
        self.frames = max(1, self.get_int("bitmapframes", 1))
        self.zero_frame = self.get_bool("bitmapzeroframe")
        self.extend = self.get_bool("bitmapextend")
        self.digits = max(0, self.get_int("bitmapdigits", 0))
        self.align = self.get("bitmapalign", "left").strip().lower()
        self.separation = self.get_int("bitmapseparation", 0)
        self.alpha = max(0.0, min(255.0, self.get_number("imagealpha", 255))) / 255.0
        self.text = ""

    def frame_size(self) -> tuple[float, float, bool]:
        if self.img is None:
            return (0.0, 0.0, False)
        vertical = self.img.height > self.img.width
        if vertical:
            return (float(self.img.width), self.img.height / self.frames, True)
        return (self.img.width / self.frames, float(self.img.height), False)

    def update_meter(self) -> None:
        fw, fh, _ = self.frame_size()
        m = self.measure_value(0)
        if self.extend:
            value = int(m.value) if m is not None else 0
            text = str(abs(value))
            if self.digits:
                text = text[-self.digits:].rjust(self.digits, "0")
            self.text = text
            n = max(len(text), self.digits or 0)
            self.natural_size(n * fw + max(0, n - 1) * self.separation, fh)
        else:
            self.natural_size(fw, fh)

    def frame_index(self) -> int:
        m = self.measure_value(0)
        v = m.relative() if m is not None else 0.0
        if self.zero_frame:
            if v <= 0:
                return 0
            return min(self.frames - 1, 1 + int(v * (self.frames - 1)))
        return min(self.frames - 1, int(v * self.frames))

    def _draw_frame(self, cr: Any, i: int, x: float, w: float, h: float) -> None:
        assert self.img is not None
        fw, fh, vertical = self.frame_size()
        src = (0.0, fh * i, fw, fh) if vertical else (fw * i, 0.0, fw, fh)
        draw_image_scaled(cr, self.img, x, 0, w, h, alpha=self.alpha, src=src)

    def draw_content(self, cr: Any) -> None:
        if self.img is None:
            return
        fw, fh, _ = self.frame_size()
        if not self.extend:
            self._draw_frame(cr, self.frame_index(), 0, self.content_w, self.content_h)
            return
        n = len(self.text)
        total = n * fw + max(0, n - 1) * self.separation
        x = 0.0
        if self.align == "right":
            x = self.content_w - total
        elif self.align == "center":
            x = (self.content_w - total) / 2
        for ch in self.text:
            d = int(ch)
            if d < self.frames:
                self._draw_frame(cr, d, x, fw, fh)
            x += fw + self.separation
