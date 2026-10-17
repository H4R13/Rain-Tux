"""Persisted per-config settings (Rainmeter keeps these in Rainmeter.ini): active file, position…"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from raintux.core.skin import WindowState

log = logging.getLogger(__name__)


def state_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "raintux" / "skins.json"


@dataclass
class ConfigState:
    active_file: str | None = None  # e.g. "Clock.ini" when the config is loaded
    load_order: int = 0
    window: WindowState = field(default_factory=WindowState)


@dataclass
class AppState:
    configs: dict[str, ConfigState] = field(default_factory=dict)
    first_run_done: bool = False

    def config(self, name: str) -> ConfigState:
        key = name.replace("/", "\\")
        for k in self.configs:
            if k.lower() == key.lower():
                return self.configs[k]
        cs = ConfigState()
        self.configs[key] = cs
        return cs

    def has(self, name: str) -> bool:
        """The config has settings already (Rainmeter's "has a section in Rainmeter.ini")."""
        key = name.replace("/", "\\").lower()
        return any(k.lower() == key for k in self.configs)

    def active(self) -> list[tuple[str, ConfigState]]:
        items = [(k, v) for k, v in self.configs.items() if v.active_file]
        items.sort(key=lambda kv: (kv[1].load_order, kv[0].lower()))
        return items


def load_state(path: Path | None = None) -> AppState:
    p = path or state_path()
    if not p.is_file():
        return AppState()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("could not read %s; starting with defaults", p)
        return AppState()
    st = AppState(first_run_done=bool(raw.get("first_run_done", False)))
    for name, cs in (raw.get("configs") or {}).items():
        st.configs[name] = ConfigState(
            active_file=cs.get("active_file"),
            load_order=int(cs.get("load_order", 0)),
            window=window_state(cs.get("window") or {}),
        )
    return st


def window_state(raw: dict) -> WindowState:
    """WindowState from saved JSON. Text in ``x`` / ``y`` (a formula, ``20R``, ``50%``) is taken
    as WindowX / WindowY as written, to be worked out when the skin is placed."""
    known = set(WindowState.__dataclass_fields__)
    win = {k: v for k, v in raw.items() if k in known}
    for axis in ("x", "y"):
        v = win.get(axis)
        if isinstance(v, (int, float)):
            win[axis] = int(v)
        elif v is not None:
            if win.get(f"window_{axis}") is None:
                win[f"window_{axis}"] = str(v)
            win[axis] = 0
    return WindowState(**win)


def save_state(state: AppState, path: Path | None = None) -> None:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "first_run_done": state.first_run_done,
        "configs": {k: {"active_file": v.active_file, "load_order": v.load_order, "window": asdict(v.window)}
                    for k, v in sorted(state.configs.items())},
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)
