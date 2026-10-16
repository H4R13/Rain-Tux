"""Rainmeter meters (drawing) and the ``Meter=`` registry."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from raintux.meters.base_meter import Meter

if TYPE_CHECKING:
    from raintux.core.skin import Skin

log = logging.getLogger(__name__)


def _registry() -> dict[str, type[Meter]]:
    from raintux.meters.bar_meter import BarMeter
    from raintux.meters.bitmap_meter import BitmapMeter
    from raintux.meters.button_meter import ButtonMeter
    from raintux.meters.graph_meters import HistogramMeter, LineMeter
    from raintux.meters.image_meter import ImageMeter
    from raintux.meters.rotator_meter import RotatorMeter
    from raintux.meters.roundline_meter import RoundlineMeter
    from raintux.meters.shape_meter import ShapeMeter
    from raintux.meters.string_meter import StringMeter

    return {
        "string": StringMeter,
        "image": ImageMeter,
        "bar": BarMeter,
        "shape": ShapeMeter,
        "roundline": RoundlineMeter,
        "rotator": RotatorMeter,
        "histogram": HistogramMeter,
        "line": LineMeter,
        "button": ButtonMeter,
        "bitmap": BitmapMeter,
    }


def create_meter(skin: Skin, name: str, raw: dict[str, str]) -> Meter:
    """Instantiate the meter class for ``Meter=`` (unknown types draw only their background)."""
    mtype = skin.resolve({k.lower(): v for k, v in raw.items()}.get("meter", "")).strip().lower()
    cls = _registry().get(mtype)
    if cls is None:
        log.warning("[%s] unknown meter type %r", name, mtype)
        cls = Meter
    return cls(skin, name, raw)
