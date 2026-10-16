"""Image loading (any Pillow format), effects, a small cache, and Cairo drawing helpers."""

from __future__ import annotations

import logging
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_CACHE: OrderedDict[tuple[Any, ...], Image | None] = OrderedDict()
_CACHE_MAX = 512
_CACHE_BYTES = 384 * 1024 * 1024  # decoded RGBA + Cairo copy, roughly 8 bytes per pixel


@dataclass(eq=False)
class Image:
    """A decoded RGBA image; the Cairo surface is created lazily on first draw."""

    pil: Any  # PIL.Image.Image in RGBA mode
    width: int
    height: int
    _surface: Any = field(default=None, repr=False)

    def surface(self) -> Any:
        if self._surface is None:
            self._surface = _to_cairo(self.pil)
        return self._surface

    def alpha_at(self, x: float, y: float) -> int:
        xi, yi = int(x), int(y)
        if not (0 <= xi < self.width and 0 <= yi < self.height):
            return 0
        return int(self.pil.getpixel((xi, yi))[3])


def _to_cairo(pil: Any) -> Any:
    """RGBA Pillow image → premultiplied ARGB32 Cairo surface (BGRA bytes on little-endian)."""
    import cairo
    from PIL import Image as PILImage

    premul = pil.convert("RGBa")
    r, g, b, a = premul.split()
    data = bytearray(PILImage.merge("RGBa", (b, g, r, a)).tobytes("raw", "RGBa"))
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, pil.width)
    if stride != pil.width * 4:
        rows = [data[i * pil.width * 4 : (i + 1) * pil.width * 4] for i in range(pil.height)]
        pad = bytes(stride - pil.width * 4)
        data = bytearray(b"".join(bytes(r) + pad for r in rows))
    return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, pil.width, pil.height, stride)


def resolve_image_path(path: Path) -> Path | None:
    """Exact path, then the same name with a common image extension (case-insensitive)."""
    if path.is_file():
        return path
    parent = path.parent
    if parent.is_dir():
        low = path.name.lower()
        for p in parent.iterdir():
            if p.name.lower() == low and p.is_file():
                return p
        if not path.suffix:
            for ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".ico", ".webp"):
                for p in parent.iterdir():
                    if p.name.lower() == low + ext:
                        return p
    return None


def load_image(
    path: Path,
    *,
    greyscale: bool = False,
    tint: tuple[float, float, float, float] | None = None,
    color_matrix: tuple[tuple[float, ...], ...] | None = None,
) -> Image | None:
    """Decode an image file with optional colour effects; results are cached by mtime."""
    real = resolve_image_path(Path(path))
    if real is None:
        log.debug("missing image %s", path)
        return None
    try:
        mtime = real.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(real), mtime, greyscale, tint, color_matrix)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    img: Image | None = None
    try:
        from PIL import Image as PILImage

        with PILImage.open(real) as im:
            im.seek(0)
            pil = im.convert("RGBA")
        if greyscale:
            a = pil.getchannel("A")
            pil = pil.convert("L").convert("RGBA")
            pil.putalpha(a)
        if color_matrix is not None:
            pil = apply_color_matrix(pil, color_matrix)
        if tint is not None and tint != (1.0, 1.0, 1.0, 1.0):
            pil = apply_tint(pil, tint)
        img = Image(pil, pil.width, pil.height)
    except Exception:
        log.warning("cannot load image %s", real, exc_info=True)
    _CACHE[key] = img
    total = sum(8 * i.width * i.height for i in _CACHE.values() if i is not None)
    while len(_CACHE) > 1 and (len(_CACHE) > _CACHE_MAX or total > _CACHE_BYTES):
        _k, old = _CACHE.popitem(last=False)
        if old is not None:
            total -= 8 * old.width * old.height
    return img


def apply_tint(pil: Any, tint: tuple[float, float, float, float]) -> Any:
    """``ImageTint``: multiply each channel (alpha included) by the tint colour."""
    channels = pil.split()
    out = [ch.point(lambda v, f=f: int(v * f + 0.5)) for ch, f in zip(channels, tint)]
    from PIL import Image as PILImage

    return PILImage.merge("RGBA", out)


def apply_color_matrix(pil: Any, m: tuple[tuple[float, ...], ...]) -> Any:
    """``ColorMatrix1..5``: 5x5 matrix on RGBA (row-vector convention, offsets in row 5)."""
    from PIL import Image as PILImage

    try:
        import numpy as np
    except ImportError:
        log.info("ColorMatrix needs numpy; skipped")
        return pil
    arr = np.asarray(pil, dtype=np.float32) / 255.0
    mat = np.array([list(r) + [0.0] * (5 - len(r)) for r in m][:5], dtype=np.float32)
    flat = arr.reshape(-1, 4)
    res = flat @ mat[:4, :4] + mat[4, :4]
    res = np.clip(res * 255.0 + 0.5, 0, 255).astype(np.uint8).reshape(arr.shape)
    return PILImage.fromarray(res, "RGBA")


def clear_cache() -> None:
    _CACHE.clear()


# --------------------------------------------------------------------------- drawing
def draw_image_scaled(
    cr: Any,
    img: Image,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    alpha: float = 1.0,
    margins: list[float] | None = None,
    tile: bool = False,
    src: tuple[float, float, float, float] | None = None,
) -> None:
    """Draw ``img`` (or its ``src`` rectangle) into x,y,w,h — stretched, 9-sliced or tiled."""
    import cairo

    if w <= 0 or h <= 0:
        return
    surf = img.surface()
    sx, sy, sw, sh = src or (0.0, 0.0, float(img.width), float(img.height))
    if sw <= 0 or sh <= 0:
        return
    cr.save()
    if tile:
        pat = cairo.SurfacePattern(surf)
        pat.set_extend(cairo.EXTEND_REPEAT)
        m = cairo.Matrix()
        m.translate(-x + sx, -y + sy)
        pat.set_matrix(m)
        cr.rectangle(x, y, w, h)
        cr.set_source(pat)
        cr.clip()
        cr.paint_with_alpha(alpha)
    elif margins and any(margins):
        _nine_slice(cr, surf, (sx, sy, sw, sh), (x, y, w, h), margins, alpha)
    else:
        cr.rectangle(x, y, w, h)
        cr.clip()
        cr.translate(x, y)
        cr.scale(w / sw, h / sh)
        cr.set_source_surface(surf, -sx, -sy)
        pat = cr.get_source()
        pat.set_filter(cairo.FILTER_GOOD if (w != sw or h != sh) else cairo.FILTER_NEAREST)
        pat.set_extend(cairo.EXTEND_PAD)
        cr.paint_with_alpha(alpha)
    cr.restore()


def _nine_slice(
    cr: Any,
    surf: Any,
    src: tuple[float, float, float, float],
    dst: tuple[float, float, float, float],
    margins: list[float],
    alpha: float,
) -> None:
    import cairo

    sx, sy, sw, sh = src
    dx, dy, dw, dh = dst
    left, top, right, bottom = (max(0.0, v) for v in margins[:4])
    xs_s = [sx, sx + left, sx + sw - right, sx + sw]
    ys_s = [sy, sy + top, sy + sh - bottom, sy + sh]
    xs_d = [dx, dx + left, dx + dw - right, dx + dw]
    ys_d = [dy, dy + top, dy + dh - bottom, dy + dh]
    for i in range(3):
        for j in range(3):
            s_w, s_h = xs_s[i + 1] - xs_s[i], ys_s[j + 1] - ys_s[j]
            d_w, d_h = xs_d[i + 1] - xs_d[i], ys_d[j + 1] - ys_d[j]
            if s_w <= 0 or s_h <= 0 or d_w <= 0 or d_h <= 0:
                continue
            cr.save()
            cr.rectangle(xs_d[i], ys_d[j], d_w, d_h)
            cr.clip()
            cr.translate(xs_d[i], ys_d[j])
            cr.scale(d_w / s_w, d_h / s_h)
            cr.set_source_surface(surf, -xs_s[i], -ys_s[j])
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint_with_alpha(alpha)
            cr.restore()


def rotated_bounds(w: float, h: float, degrees: float) -> tuple[float, float]:
    a = math.radians(degrees)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return (w * c + h * s, w * s + h * c)
