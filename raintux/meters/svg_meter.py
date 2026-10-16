"""``Meter=Svg``: render an SVG file, optionally rewriting attributes first.

``SvgImage=file.svg``, ``Attribute=selector | attribute | value`` (``AttributeN``…;
selectors: ``#id``, ``.class``, ``tag`` or ``*``) and ``PreserveAspectRatio`` (0 stretch,
1 fit, 2 fill). Uses librsvg (``gir1.2-rsvg-2.0``) when present, else GdkPixbuf's SVG loader.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from raintux.meters.base_meter import Meter

log = logging.getLogger(__name__)
_SVG_NS = "http://www.w3.org/2000/svg"


def _matches(el: ET.Element, selector: str) -> bool:
    tag = el.tag.rsplit("}", 1)[-1]
    if selector == "*":
        return True
    if selector.startswith("#"):
        return el.get("id") == selector[1:]
    if selector.startswith("."):
        return selector[1:] in (el.get("class") or "").split()
    return tag == selector


def apply_attributes(data: bytes, attrs: list[tuple[str, str, str]]) -> bytes:
    if not attrs:
        return data
    ET.register_namespace("", _SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    root = ET.fromstring(data)
    for el in root.iter():
        for selector, attribute, value in attrs:
            if _matches(el, selector):
                el.set(attribute, value)
    return ET.tostring(root)


class _Renderer:
    def __init__(self, data: bytes) -> None:
        self.handle: Any = None
        self.pixbuf_loader = False
        self.width = self.height = 0.0
        try:
            import gi

            gi.require_version("Rsvg", "2.0")
            from gi.repository import Rsvg

            self.handle = Rsvg.Handle.new_from_data(data)
            ok, w, h = self.handle.get_intrinsic_size_in_pixels()
            if not ok:
                dim = self.handle.get_dimensions()
                w, h = dim.width, dim.height
            self.width, self.height = float(w), float(h)
            return
        except (ImportError, ValueError):
            pass
        from gi.repository import GdkPixbuf

        loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
        loader.write(data)
        loader.close()
        self.pixbuf = loader.get_pixbuf()
        self.width, self.height = float(self.pixbuf.get_width()), float(self.pixbuf.get_height())
        self.pixbuf_loader = True

    def draw(self, cr: Any, x: float, y: float, w: float, h: float) -> None:
        if self.handle is not None:
            from gi.repository import Rsvg

            viewport = Rsvg.Rectangle()
            viewport.x, viewport.y, viewport.width, viewport.height = x, y, w, h
            self.handle.render_document(cr, viewport)
            return
        from gi.repository import Gdk

        pb = self.pixbuf.scale_simple(max(1, int(w)), max(1, int(h)), 2)
        cr.save()
        Gdk.cairo_set_source_pixbuf(cr, pb, x, y)
        cr.paint()
        cr.restore()


class SvgMeter(Meter):
    def read_meter(self) -> None:
        self.preserve = self.get_int("preserveaspectratio", 0)
        src = self.get("svgimage")
        attrs = []
        i = 1
        while True:
            key = "attribute" if i == 1 else f"attribute{i}"
            val = self.get(key)
            if not val:
                break
            parts = [p.strip() for p in val.split("|", 2)]
            if len(parts) == 3 and parts[0] and parts[1]:
                attrs.append((parts[0], parts[1], parts[2]))
            else:
                log.warning("[%s] invalid %s=%s", self.name, key, val)
            i += 1
        key = (src, tuple(attrs))
        if getattr(self, "_key", None) != key:
            self._key = key
            self.renderer: _Renderer | None = None
            if src:
                path = self.skin.path(src)
                try:
                    data = apply_attributes(Path(path).read_bytes(), attrs)
                    self.renderer = _Renderer(data)
                except Exception as e:
                    log.warning("[%s] cannot load SVG %s: %s", self.name, path, e)

    def update_meter(self) -> None:
        r = getattr(self, "renderer", None)
        if r is None:
            self.natural_size(0, 0)
            return
        w, h = self.w_opt, self.h_opt
        if w is None and h is None:
            w, h = r.width, r.height
        elif w is None:
            w = (h or 0) * r.width / r.height if r.height else 0
        elif h is None:
            h = w * r.height / r.width if r.width else 0
        self.content_w, self.content_h = float(w), float(h)

    def draw_content(self, cr: Any) -> None:
        r = getattr(self, "renderer", None)
        if r is None or self.content_w <= 0 or self.content_h <= 0:
            return
        bw, bh = self.content_w, self.content_h
        x, y, w, h = 0.0, 0.0, bw, bh
        if self.preserve in (1, 2) and r.width and r.height:
            k = min(bw / r.width, bh / r.height) if self.preserve == 1 else max(bw / r.width, bh / r.height)
            w, h = r.width * k, r.height * k
            x, y = (bw - w) / 2, (bh - h) / 2
        cr.save()
        cr.rectangle(0, 0, bw, bh)
        cr.clip()
        if self.preserve == 0 and r.handle is not None:
            # Stretch: scale the SVG's own viewport non-uniformly.
            cr.scale(bw / r.width, bh / r.height)
            r.draw(cr, 0, 0, r.width, r.height)
        else:
            r.draw(cr, x, y, w, h)
        cr.restore()
