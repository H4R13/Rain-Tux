"""``Meter=Button``: three-frame image (normal / pressed / hover) that runs ``ButtonCommand``."""

from __future__ import annotations

from typing import Any

from raintux.meters.base_meter import Meter
from raintux.meters.image_meter import image_options
from raintux.meters.images import Image, draw_image_scaled, load_image


class ButtonMeter(Meter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pressed = False
        self.focused = False

    def read_meter(self) -> None:
        name = self.get("buttonimage")
        self.img: Image | None = load_image(self.skin.path(name), **image_options(self)) if name else None
        self.alpha = max(0.0, min(255.0, self.get_number("imagealpha", 255))) / 255.0

    def frame_size(self) -> tuple[float, float, bool]:
        if self.img is None:
            return (0.0, 0.0, False)
        horizontal = self.img.width > self.img.height
        if horizontal:
            return (self.img.width / 3.0, float(self.img.height), True)
        return (float(self.img.width), self.img.height / 3.0, False)

    def update_meter(self) -> None:
        fw, fh, _ = self.frame_size()
        self.natural_size(fw, fh)

    def frame(self) -> int:
        if self.pressed and self.focused:
            return 1
        if self.focused and not self.pressed:
            return 2
        return 0

    def draw_content(self, cr: Any) -> None:
        if self.img is None:
            return
        fw, fh, horizontal = self.frame_size()
        i = self.frame()
        src = (fw * i, 0.0, fw, fh) if horizontal else (0.0, fh * i, fw, fh)
        draw_image_scaled(cr, self.img, 0, 0, self.content_w, self.content_h, alpha=self.alpha, src=src)

    def hit_content(self, x: float, y: float) -> bool:
        if self.img is None:
            return False
        fw, fh, horizontal = self.frame_size()
        if self.content_w <= 0 or self.content_h <= 0:
            return False
        ix = x * fw / self.content_w
        iy = y * fh / self.content_h
        return self.img.alpha_at(ix, iy) > 0
