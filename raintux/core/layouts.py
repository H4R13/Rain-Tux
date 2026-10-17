"""Layouts: named snapshots of which skins are loaded and where (Rainmeter's Layouts tab).

Stored as ``~/RainTux/Layouts/<Name>/layout.json``. Rainmeter layouts
(``Layouts/<Name>/Rainmeter.ini``, e.g. from an .rmskin) can be loaded too.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from raintux.compat.ini_normalizer import read_ini_normalized
from raintux.core.position import Rect, authored_position
from raintux.core.skin import WindowState
from raintux.core.state import AppState, ConfigState, window_state

log = logging.getLogger(__name__)

_INVALID = re.compile(r'[\\/:*?"<>|]')


def list_layouts(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    out = [p.name for p in root.iterdir()
           if p.is_dir() and ((p / "layout.json").is_file() or (p / "Rainmeter.ini").is_file())]
    return sorted(out, key=str.lower)


def save_layout(root: Path, name: str, state: AppState) -> Path:
    clean = _INVALID.sub("_", name.strip()) or "Layout"
    d = root / clean
    d.mkdir(parents=True, exist_ok=True)
    data = {k: {"active_file": v.active_file, "load_order": v.load_order, "window": asdict(v.window)}
            for k, v in state.configs.items() if v.active_file}
    (d / "layout.json").write_text(json.dumps({"configs": data}, indent=2), encoding="utf-8")
    return d


def _num(value: str, default: int = 0) -> int:
    m = re.match(r"\s*(-?\d+)", value or "")
    return int(m.group(1)) if m else default


def read_layout(root: Path, name: str, list_files: object = None,
                monitors: Sequence[Rect] = ()) -> dict[str, ConfigState]:
    """Configs of a layout. ``list_files(config) -> [ini names]`` maps Rainmeter's ``Active=N``.

    WindowX / WindowY / AnchorX / AnchorY of a Rainmeter layout are kept as written (they can be
    formulas like ``(#WORKAREAWIDTH# - 361)``, or ``20R``, ``50%``, ``@2``); ``x`` / ``y`` hold
    where they land on ``monitors`` until the engine places the skin with its real size.
    """
    d = root / name
    out: dict[str, ConfigState] = {}
    if (d / "layout.json").is_file():
        raw = json.loads((d / "layout.json").read_text(encoding="utf-8"))
        for cfg, cs in (raw.get("configs") or {}).items():
            out[cfg] = ConfigState(cs.get("active_file"), int(cs.get("load_order", 0)),
                                   window_state(cs.get("window") or {}))
        return out
    ini = d / "Rainmeter.ini"
    if not ini.is_file():
        raise FileNotFoundError(name)
    section: str | None = None
    values: dict[str, dict[str, str]] = {}
    for line in read_ini_normalized(ini).split("\n"):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            values.setdefault(section, {})
        elif "=" in s and section is not None and not s.startswith(";"):
            k, _sep, v = s.partition("=")
            values[section][k.strip().lower()] = v.strip()
    for cfg, opts in values.items():
        if cfg.lower() in ("rainmeter", "tray", "trayicon") or "active" not in opts:
            continue
        active = _num(opts.get("active", "0"))
        if active <= 0:
            continue
        files = list_files(cfg) if callable(list_files) else []
        if not files or active > len(files):
            continue
        win = WindowState(
            window_x=opts.get("windowx", "0"), window_y=opts.get("windowy", "0"),
            anchor_x=opts.get("anchorx", "0"), anchor_y=opts.get("anchory", "0"),
            z=_num(opts.get("alwaysontop", "0")), draggable=opts.get("draggable", "1") != "0",
            click_through=opts.get("clickthrough", "0") == "1", keep_on_screen=opts.get("keeponscreen", "1") != "0",
            save_position=opts.get("saveposition", "1") != "0", snap_edges=opts.get("snapedges", "1") != "0",
            alpha=_num(opts.get("alphavalue", "255"), 255), fade_duration=_num(opts.get("fadeduration", "250"), 250),
            hide_on_mouse_over=opts.get("hideonmouseover", "0") == "1",
        )
        win.x, win.y = authored_position(win, (0, 0), monitors) or (0, 0)
        out[cfg] = ConfigState(files[active - 1], _num(opts.get("loadorder", "0")), win)
    return out
