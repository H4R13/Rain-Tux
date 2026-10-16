"""Lay out meters and paint them to a Cairo context."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import cairo

from raintux.core.skin_parser import ParsedSkin
from raintux.meters.bar_meter import draw_bar_meter
from raintux.meters.base_meter import MeterLayout, parse_coord
from raintux.meters.image_meter import draw_image_meter
from raintux.meters.shape_meter import draw_shape_meter
from raintux.meters.string_meter import draw_string_meter

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


# Size used when a meter sets no W/H (0 means "natural size" for images).
_DEFAULT_SIZE: dict[str, tuple[int, int]] = {
    "image": (0, 0),
    "bar": (150, 14),
}
_FALLBACK_SIZE = (120, 30)


def _parse_size(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(float(value))
    except ValueError:
        log.debug("unparseable size %r, using %d", value, default)
        return default


def compute_layout(parsed: ParsedSkin) -> list[MeterLayout]:
    """
    Compute pixel layout for each meter in skin order.

    ``W``/``H`` default by meter kind when unset or unparseable (e.g. formulas).
    """
    prev_r = 0
    prev_b = 0
    prev_x = 0
    prev_y = 0

    out: list[MeterLayout] = []
    for name, section in parsed.meters:
        expanded: dict[str, str] = {}
        for k, v in section.items():
            expanded[k] = parsed.variable_text(v)
        ecf = {k.lower(): v for k, v in expanded.items()}
        mtype = ecf.get("meter", "string").strip().lower()

        x = parse_coord(ecf.get("x", "0"), prev_x, prev_r, prev_x)
        y = parse_coord(ecf.get("y", "0"), prev_y, prev_b, prev_y)
        dw, dh = _DEFAULT_SIZE.get(mtype, _FALLBACK_SIZE)
        w = _parse_size(ecf.get("w"), dw)
        h = _parse_size(ecf.get("h"), dh)

        out.append(MeterLayout(name=name, mtype=mtype, x=x, y=y, w=w, h=h, raw=expanded))
        prev_x, prev_y = x, y
        prev_r = x + w
        prev_b = y + h
    return out


def render_skin(
    cr: cairo.Context,
    parsed: ParsedSkin,
    get_measure: Callable[[str], str],
    layouts: list[MeterLayout] | None = None,
) -> None:
    """Paint all meters for the given skin."""
    lays = layouts or compute_layout(parsed)
    cr.save()
    cr.set_operator(cairo.OPERATOR_OVER)
    for layout in lays:
        try:
            if layout.mtype == "string":
                draw_string_meter(cr, layout, get_measure)
            elif layout.mtype == "image":
                draw_image_meter(cr, layout, parsed.root)
            elif layout.mtype in ("shape", "roundline"):
                draw_shape_meter(cr, layout)
            elif layout.mtype == "bar":
                draw_bar_meter(cr, layout, get_measure)
            else:
                log.debug("unknown meter type %s [%s]", layout.mtype, layout.name)
        except Exception:
            log.exception("meter render failed %s", layout.name)
    cr.restore()
