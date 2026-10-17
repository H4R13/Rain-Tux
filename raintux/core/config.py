"""Global application configuration loaded from TOML (XDG config path)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import tomllib
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def _xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


DEFAULT_CONFIG_PATH = _xdg_config_home() / "raintux" / "config.toml"


class ApiConfig(BaseModel):
    """Local HTTP API binding (used by the web Skin Manager and scripts)."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 7272


class EngineConfig(BaseModel):
    """Core engine options."""

    default_update_ms: int = Field(default=1000, ge=16)
    skins_roots: list[str] = Field(default_factory=list)


class UiConfig(BaseModel):
    """Desktop integration."""

    tray: bool = True
    editor: str = ""  # command used by "Edit skin"; empty = $VISUAL / $EDITOR / default app


class RainTuxConfig(BaseModel):
    """Top-level config file schema."""

    api: ApiConfig = Field(default_factory=ApiConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    # Windows drive letter -> Linux folder for skins made on Windows (``D = "/home"``).
    drives: dict[str, str] = Field(default_factory=dict)


def skins_root() -> Path:
    """Rainmeter-format skins (INI files, Lua scripts), like ``Documents\\Rainmeter\\Skins``."""
    return Path.home() / "RainTux" / "Skins"


def python_skins_root() -> Path:
    """Skins whose scripts are Python (``ScriptFile=*.py``), kept apart from the Lua ones."""
    return Path.home() / "RainTux" / "PythonSkins"


def default_skins_roots() -> list[Path]:
    """Default search paths for skins (XDG + legacy ~/RainTux)."""
    roots = [
        skins_root(),
        python_skins_root(),
        _xdg_data_home() / "raintux" / "skins",
        Path("/usr/share/raintux/skins") if Path("/usr/share/raintux/skins").exists() else None,
    ]
    return [p for p in roots if p is not None]


def layouts_root() -> Path:
    return Path.home() / "RainTux" / "Layouts"


def ensure_layout() -> None:
    """Create config dir, data dir, and default skins folders if missing."""
    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = _xdg_data_home() / "raintux"
    (data / "logs").mkdir(parents=True, exist_ok=True)
    skins_root().mkdir(parents=True, exist_ok=True)
    python_skins_root().mkdir(parents=True, exist_ok=True)
    layouts_root().mkdir(parents=True, exist_ok=True)


def load_config(path: Path | None = None) -> RainTuxConfig:
    """Load TOML config; write defaults when missing; keep a broken file aside and use defaults."""
    ensure_layout()
    cfg_path = path or DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        default = RainTuxConfig()
        default.engine.skins_roots = [str(p) for p in default_skins_roots()]
        save_config(default, cfg_path)
        return default
    try:
        raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        cfg = RainTuxConfig.model_validate(raw)
    except (tomllib.TOMLDecodeError, ValidationError, UnicodeDecodeError, OSError) as e:
        backup = cfg_path.with_suffix(".toml.broken")
        log.error("config %s is invalid (%s); using defaults, old file kept as %s", cfg_path, e, backup.name)
        try:
            cfg_path.replace(backup)
        except OSError:
            pass
        cfg = RainTuxConfig()
        cfg.engine.skins_roots = [str(p) for p in default_skins_roots()]
        save_config(cfg, cfg_path)
    if not cfg.engine.skins_roots:
        cfg.engine.skins_roots = [str(p) for p in default_skins_roots()]
    return cfg


def _s(value: str) -> str:
    """TOML basic string (JSON escaping is a valid subset)."""
    return json.dumps(value, ensure_ascii=False)


def save_config(config: RainTuxConfig, path: Path | None = None) -> None:
    """Persist config as TOML."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[api]",
        f"enabled = {'true' if config.api.enabled else 'false'}",
        f"host = {_s(config.api.host)}",
        f"port = {int(config.api.port)}",
        "",
        "[engine]",
        f"default_update_ms = {int(config.engine.default_update_ms)}",
        "skins_roots = [",
    ]
    for root in config.engine.skins_roots:
        lines.append(f"  {_s(root)},")
    lines += [
        "]",
        "",
        "[ui]",
        f"tray = {'true' if config.ui.tray else 'false'}",
        f"editor = {_s(config.ui.editor)}",
        "",
        "# Which Linux folder a Windows drive letter in a skin means. Without an entry, C: is /, a",
        "# letter with a /mnt/<letter> mount uses it (WSL), and the others are the extra drives",
        '# under /media, /mnt and /run/media in order. Example:  D = "/home"   X = "/mnt/data"',
        "[drives]",
    ]
    for letter, folder in sorted(config.drives.items()):
        lines.append(f"{letter.strip().rstrip(':').upper()} = {_s(folder)}")
    tmp = cfg_path.with_suffix(".toml.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(cfg_path)
