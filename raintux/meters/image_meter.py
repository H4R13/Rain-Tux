"""``Meter=Image``: scaling modes, 9-slice, tiling, crop, flip, rotate, tint, greyscale, ColorMatrix."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from raintux.core.formula import parse_number
from raintux.meters.base_meter import Meter, parse_numbers, split_top_level
from raintux.meters.images import Image, draw_image_scaled, load_image, rotated_bounds


def read_color_matrix(meter: Meter) -> tuple[tuple[float, ...], ...] | None:
    rows = []
    for i in range(1, 6):
        v = meter.get(f"colormatrix{i}")
        if not v:
            if i == 1:
                return None
            rows.append(tuple(1.0 if j == i - 1 else 0.0 for j in range(5)))
            continue
        nums = [parse_number(x, 0.0) for x in v.split(";")]
        rows.append(tuple((nums + [0.0] * 5)[:5]))
    return tuple(rows)


def image_options(meter: Meter, prefix: str = "") -> dict[str, Any]:
    """Shared image effect options (``ImageTint``, ``Greyscale``, ``ColorMatrix``…)."""
    tint = meter.get_color(prefix + "imagetint", (1, 1, 1, 1)) if meter.has(prefix + "imagetint") else None
    return {
        "greyscale": meter.get_bool(prefix + "greyscale"),
        "tint": tint,
        "color_matrix": read_color_matrix(meter) if not prefix else None,
    }


class ImageMeter(Meter):
    def read_meter(self) -> None:
        self.preserve = self.get_int("preserveaspectratio", 0)
        self.tile = self.get_bool("tile")
        margins = parse_numbers(self.get("scalemargins"), 4)
        self.margins = margins if any(margins) else None
        self.alpha = max(0.0, min(255.0, self.get_number("imagealpha", 255))) / 255.0
        self.flip = self.get("imageflip", "none").strip().lower()
        self.rotate = self.get_number("imagerotate", 0.0)
        crop = split_top_level(self.get("imagecrop"), ",") if self.get("imagecrop").strip() else []
        self.crop = [parse_number(c, 0.0, self.skin.measure_number) for c in crop] if len(crop) >= 4 else None
        self.effects = image_options(self)
        self.img: Image | None = None
        self.src: tuple[float, float, float, float] | None = None

    def image_file(self) -> Path | None:
        name = self.skin.resolve(self.option_raw("imagename") or "", section_vars=self.dynamic)
        if self.measures:
            mstr = self.measures[0].string_value()
            name = name.replace("%1", mstr) if "%1" in name else (name or mstr)
        if not name:
            return None
        base = self.get("imagepath")
        if base:
            name = base.rstrip("/\\") + "/" + name
        return self.skin.path(name)

    def update_meter(self) -> None:
        path = self.image_file()
        self.img = load_image(path, **self.effects) if path else None
        if self.img is None:
            self.natural_size(0, 0)
            return
        iw, ih = float(self.img.width), float(self.img.height)
        self.src = None
        if self.crop:
            cx, cy, cw, ch = self.crop[:4]
            origin = int(self.crop[4]) if len(self.crop) > 4 else 1
            if origin in (2, 3):
                cx += iw
            if origin in (3, 4):
                cy += ih
            if origin == 5:
                cx += iw / 2
                cy += ih / 2
            self.src = (cx, cy, cw, ch)
            iw, ih = cw, ch
        if self.rotate:
            iw, ih = rotated_bounds(iw, ih, self.rotate)
        w, h = self.w_opt, self.h_opt
        if w is None and h is None:
            w, h = iw, ih
        elif w is None:
            w = (h or 0) * iw / ih if ih else 0
        elif h is None:
            h = w * ih / iw if iw else 0
        self.content_w, self.content_h = float(w), float(h)

    def _dest(self) -> tuple[float, float, float, float]:
        """Destination rect for the (cropped, unrotated) image inside the content area."""
        assert self.img is not None
        sw = self.src[2] if self.src else self.img.width
        sh = self.src[3] if self.src else self.img.height
        bw, bh = self.content_w, self.content_h
        if self.rotate:
            rw, rh = rotated_bounds(sw, sh, self.rotate)
            k = min(bw / rw, bh / rh) if rw and rh else 1.0
            return ((bw - sw * k) / 2, (bh - sh * k) / 2, sw * k, sh * k)
        if self.preserve in (1, 2) and sw and sh and not self.tile:
            k = min(bw / sw, bh / sh) if self.preserve == 1 else max(bw / sw, bh / sh)
            return ((bw - sw * k) / 2, (bh - sh * k) / 2, sw * k, sh * k)
        return (0.0, 0.0, bw, bh)

    def draw_content(self, cr: Any) -> None:
        if self.img is None or self.content_w <= 0 or self.content_h <= 0:
            return
        x, y, w, h = self._dest()
        cr.save()
        if self.preserve == 2:
            cr.rectangle(0, 0, self.content_w, self.content_h)
            cr.clip()
        cx, cy = x + w / 2, y + h / 2
        if self.flip in ("horizontal", "both", "vertical"):
            cr.translate(cx, cy)
            cr.scale(-1 if self.flip in ("horizontal", "both") else 1, -1 if self.flip in ("vertical", "both") else 1)
            cr.translate(-cx, -cy)
        if self.rotate:
            cr.translate(cx, cy)
            cr.rotate(math.radians(self.rotate))
            cr.translate(-cx, -cy)
        draw_image_scaled(cr, self.img, x, y, w, h, alpha=self.alpha, margins=self.margins, tile=self.tile, src=self.src)
        cr.restore()

    def hit_content(self, x: float, y: float) -> bool:
        if self.solid[3] > 0:
            return True
        if self.img is None:
            return False
        dx, dy, dw, dh = self._dest()
        if self.tile or self.rotate or dw <= 0 or dh <= 0:
            return True
        if self.flip in ("horizontal", "both"):
            x = dx + dw - (x - dx)
        if self.flip in ("vertical", "both"):
            y = dy + dh - (y - dy)
        sx0, sy0, sw, sh = self.src or (0.0, 0.0, float(self.img.width), float(self.img.height))
        ix = sx0 + (x - dx) * sw / dw
        iy = sy0 + (y - dy) * sh / dh
        return self.img.alpha_at(ix, iy) > 0
