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
        mask = self.get("maskimagename")
        if mask:
            base = self.get("maskimagepath")
            if base:
                mask = base.rstrip("/\\") + "/" + mask
            self.mask: Image | None = load_image(self.skin.path(mask))
        else:
            self.mask = None
        self.mask_flip = self.get("maskimageflip", "none").strip().lower()
        self.mask_rotate = self.get_number("maskimagerotate", 0.0)

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
        if self.mask is not None:
            cr.push_group()
        draw_image_scaled(cr, self.img, x, y, w, h, alpha=self.alpha, margins=self.margins, tile=self.tile, src=self.src)
        cr.restore()
        if self.mask is not None:
            self._apply_mask(cr)

    def _apply_mask(self, cr: Any) -> None:
        """``MaskImageName``: the image keeps only where the mask (scaled to the meter) is opaque."""
        import cairo

        content = cr.pop_group()
        assert self.mask is not None
        mw, mh = float(self.mask.width), float(self.mask.height)
        bw, bh = self.content_w, self.content_h
        if mw <= 0 or mh <= 0 or bw <= 0 or bh <= 0:
            return
        pat = cairo.SurfacePattern(self.mask.surface())
        pat.set_filter(cairo.FILTER_GOOD)
        m = cairo.Matrix()
        # pattern space = mask pixels; user space = content box
        m.translate(mw / 2, mh / 2)
        if self.mask_rotate:
            m.rotate(-math.radians(self.mask_rotate))
        fx = -1 if self.mask_flip in ("horizontal", "both") else 1
        fy = -1 if self.mask_flip in ("vertical", "both") else 1
        m.scale(fx * mw / bw, fy * mh / bh)
        m.translate(-bw / 2, -bh / 2)
        pat.set_matrix(m)
        cr.save()
        cr.set_source(content)
        cr.mask(pat)
        cr.restore()
