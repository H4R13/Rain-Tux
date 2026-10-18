"""Linux equivalents of Windows plugins about the desktop itself: ConfigActive (which configs are
loaded), SysColor (the accent colour) and FrostedGlass (blur behind a skin, which Wayland
compositors don't take from clients: drawn as is)."""

from __future__ import annotations

import logging
import re
from typing import Any

from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)


class ConfigActivePlugin(Measure):
    """``ConfigName=Config``: 1 when it is loaded, -1 when not. ``[&Measure:IsActive(Config)]``
    and ``[&Measure:ConfigVariantName(Config)]`` (the loaded file) answer for any config."""

    def read_measure(self) -> None:
        self.config_name = self.get("configname").strip()

    def _loaded(self, config: str) -> Any:
        return self.skin.host.find_skin(config.strip().strip("'\""))

    def update_value(self) -> None:
        self._raw_number = (1.0 if self._loaded(self.config_name) else -1.0) if self.config_name else 0.0

    def inline_call(self, call: str) -> str:
        m = re.match(r"\s*(\w+)\s*\((.*)\)\s*$", call, re.S)
        if not m:
            return ""
        func, arg = m.group(1).lower(), m.group(2)
        skin = self._loaded(arg)
        if func == "isactive":
            return "1" if skin else "-1"
        if func == "configvariantname":
            return skin.ini_path.name if skin else ""
        return ""


class SysColorPlugin(Measure):
    """``ColorType``: DWM_COLOR / accent colours from the desktop (see compat/windows.py), or
    window / text colours of the light or dark theme. ``DisplayType``: RGB (``r,g,b``), HEX,
    or one channel (R, G, B)."""

    _THEME = {  # (light, dark) for the classic GetSysColor names skins ask for
        "color_window": ((255, 255, 255), (32, 32, 32)), "color_windowtext": ((0, 0, 0), (255, 255, 255)),
        "color_btnface": ((240, 240, 240), (43, 43, 43)), "color_btntext": ((0, 0, 0), (255, 255, 255)),
        "color_menu": ((240, 240, 240), (43, 43, 43)), "color_menutext": ((0, 0, 0), (255, 255, 255)),
        "color_highlighttext": ((255, 255, 255), (255, 255, 255)),
    }

    def read_measure(self) -> None:
        self.color_type = self.get("colortype", "DWM_COLOR").strip().lower()
        self.display = self.get("displaytype", "RGB").strip().lower()

    def update_value(self) -> None:
        from raintux.compat.windows import accent_palette, light_theme

        if self.color_type in self._THEME:
            rgb = self._THEME[self.color_type][0 if light_theme() else 1]
        else:  # DWM_COLOR, the accent and COLOR_HIGHLIGHT / COLOR_HOTLIGHT
            rgb = accent_palette()[3]
        r, g, b = rgb
        if self.display in ("r", "g", "b"):
            self._raw_number = float({"r": r, "g": g, "b": b}[self.display])
            self.string = None
        elif self.display == "hex":
            self.string = f"{r:02X}{g:02X}{b:02X}"
        else:
            self.string = f"{r},{g},{b}"


class NoEffectPlugin(Measure):
    """Plugins that only change how a window looks on Windows (FrostedGlass's blur behind the
    skin): the skin is drawn as is. The value is 0."""

    def initialize(self) -> None:
        log.debug("[%s] %s has no effect on Linux", self.name, self.get("plugin"))

    def update_value(self) -> None:
        self._raw_number = 0.0
