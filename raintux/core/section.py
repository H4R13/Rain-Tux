"""Common option handling for measures and meters (one ``[Section]`` of a skin)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from raintux.core.formula import parse_number

if TYPE_CHECKING:
    from raintux.core.skin import Skin

log = logging.getLogger(__name__)

# #CURRENTSECTION# and the keyed [#CURRENTSECTION] are the section's own name.
_CURRENT_SECTION = re.compile(r"#CURRENTSECTION#|\[#CURRENTSECTION\]", re.IGNORECASE)


def parse_color(value: str, default: tuple[float, float, float, float] = (0, 0, 0, 0)) -> tuple[float, float, float, float]:
    """``R,G,B[,A]`` (0-255) or hex ``RRGGBB[AA]`` (with or without ``#``) as Cairo floats."""
    v = value.strip()
    if not v:
        return default
    if "," not in v:
        h = v.lstrip("#")
        if len(h) in (6, 8) and all(c in "0123456789abcdefABCDEF" for c in h):
            parts = [int(h[i : i + 2], 16) for i in range(0, len(h), 2)]
            if len(parts) == 3:
                parts.append(255)
            return (parts[0] / 255.0, parts[1] / 255.0, parts[2] / 255.0, parts[3] / 255.0)
        return default
    nums: list[float] = []
    for p in v.split(","):
        try:
            nums.append(parse_number(p.strip()))
        except ValueError:
            return default
    if len(nums) < 3:
        return default
    if len(nums) == 3:
        nums.append(255)
    return tuple(max(0.0, min(255.0, n)) / 255.0 for n in nums[:4])  # type: ignore[return-value]


class Section:
    """A measure or meter: raw INI options, ``!SetOption`` overrides, and resolved values."""

    kind = "section"

    def __init__(self, skin: Skin, name: str, raw: dict[str, str]) -> None:
        self.skin = skin
        self.name = name
        self.raw = {k.lower(): v for k, v in raw.items()}
        self.overrides: dict[str, str] = {}
        self.opts: dict[str, str] = {}
        self.dynamic = False
        self.groups: set[str] = set()
        self.update_divider = 1
        self._update_counter = 0
        self.needs_read = True

    # --- raw option lookup -------------------------------------------------
    def styles(self) -> list[dict[str, str]]:
        """Option sources consulted after the section itself (MeterStyle)."""
        return []

    def option_raw(self, key: str) -> str | None:
        key = key.lower()
        if key in self.overrides:
            return self.overrides[key]
        if key in self.raw:
            return self.raw[key]
        for style in self.styles():
            if key in style:
                return style[key]
        return None

    def all_keys(self) -> list[str]:
        keys = dict.fromkeys(self.raw)
        for style in self.styles():
            keys.update(dict.fromkeys(style))
        keys.update(dict.fromkeys(self.overrides))
        return list(keys)

    def set_option(self, key: str, value: str) -> None:
        self.overrides[key.lower()] = value
        self.needs_read = True

    # --- resolved values -----------------------------------------------------
    def read(self) -> None:
        """Resolve every option's variables (and section variables when dynamic)."""
        dv = self.option_raw("dynamicvariables")
        self.dynamic = dv is not None and parse_number(self.skin.resolve(dv), 0.0) != 0
        always = self.always_dynamic()
        self.opts = {}
        for key in self.all_keys():
            raw = self.option_raw(key)
            if raw is None:
                continue
            if "currentsection" in raw.lower():
                raw = _CURRENT_SECTION.sub(lambda _m: self.name, raw)
            # Bang options get their [section variables] when they run, not now.
            is_action = "action" in key or key == "buttoncommand"
            self.opts[key] = self.skin.resolve(raw, section_vars=not is_action and (self.dynamic or key in always))
        self.groups = {g.strip().lower() for g in self.get("group").split("|") if g.strip()}
        div = self.get_int("updatedivider", self.default_update_divider())
        self.update_divider = div if div != 0 else 1
        self.read_options()
        self.needs_read = False

    def always_dynamic(self) -> set[str]:
        """Options that resolve section variables even without DynamicVariables."""
        return set()

    def default_update_divider(self) -> int:
        return 1

    def read_options(self) -> None:
        """Subclasses read their specific options from :attr:`opts` here."""

    def get(self, key: str, default: str = "") -> str:
        return self.opts.get(key.lower(), default)

    def has(self, key: str) -> bool:
        return key.lower() in self.opts

    def get_number(self, key: str, default: float = 0.0) -> float:
        v = self.opts.get(key.lower())
        if v is None:
            return default
        return parse_number(v, default, self.skin.measure_number)

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.get_number(key, default))

    def get_bool(self, key: str, default: bool = False) -> bool:
        if key.lower() not in self.opts:
            return default
        return self.get_number(key, 1.0 if default else 0.0) != 0

    def get_color(self, key: str, default: tuple[float, float, float, float] = (0, 0, 0, 0)) -> tuple[float, float, float, float]:
        v = self.opts.get(key.lower())
        return parse_color(v, default) if v is not None else default

    def in_group(self, group: str) -> bool:
        return group.strip().lower() in self.groups

    # --- update-divider bookkeeping -----------------------------------------
    def due(self) -> bool:
        """True when this section should update on the current skin tick."""
        if self.update_divider < 0:
            due = self._update_counter == 0
        else:
            due = self._update_counter % max(1, self.update_divider) == 0
        self._update_counter += 1
        return due

    def __repr__(self) -> str:
        return f"<{type(self).__name__} [{self.name}]>"
