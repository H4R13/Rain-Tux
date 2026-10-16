"""GTK application + asyncio measure loop + skin runtimes."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gio, Gtk

from raintux.core.config import RainTuxConfig, default_skins_roots, load_config
from raintux.core.event_bus import EventBus
from raintux.core.measure_manager import MeasureManager
from raintux.core.meter_renderer import compute_layout, render_skin
from raintux.core.skin_parser import parse_skin_ini
from raintux.wayland.compositor_detect import CompositorKind, detect_compositor
from raintux.wayland.gnome_backend import create_gnome_overlay
from raintux.wayland.kde_backend import create_kde_overlay
from raintux.wayland.wlr_backend import create_overlay_window

log = logging.getLogger(__name__)


def _skin_bounds(layouts: list[Any]) -> tuple[int, int]:
    if not layouts:
        return 320, 120
    r = max(l.x + max(1, l.w) for l in layouts)
    b = max(l.y + max(1, l.h) for l in layouts)
    return max(160, r + 8), max(64, b + 8)


@dataclass
class SkinRuntime:
    """One loaded skin: parsed INI, window, layout cache."""

    skin_id: str
    ini_path: Path
    parsed: Any
    window: Gtk.ApplicationWindow
    layouts: list[Any] = field(default_factory=list)


class RainTuxEngine:
    """Coordinates skins, measures, and drawing."""

    def __init__(self, config: RainTuxConfig, app: Gtk.Application) -> None:
        self.config = config
        self.app = app
        self.event_bus = EventBus()
        self.measure_manager = MeasureManager(self.event_bus, config.engine.default_update_ms)
        self.skins: dict[str, SkinRuntime] = {}
        self._compositor = detect_compositor()
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_thread: threading.Thread | None = None

    @property
    def compositor(self) -> CompositorKind:
        return self._compositor

    def start_background_loop(self) -> None:
        """Start a dedicated asyncio event loop thread for measures."""
        if self._async_thread and self._async_thread.is_alive():
            return
        ready = threading.Event()

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._async_loop = loop
            self.measure_manager.attach_loop(loop)
            ready.set()
            loop.run_forever()

        self._async_thread = threading.Thread(target=runner, name="raintux-async", daemon=True)
        self._async_thread.start()
        ready.wait(timeout=5.0)

    def shutdown(self) -> None:
        """Cancel measure tasks and stop the asyncio loop."""
        if self._async_loop and self._async_loop.is_running():
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        if self._async_thread:
            self._async_thread.join(timeout=2.0)

    def _new_overlay(self, title: str, width: int, height: int, skin_id: str) -> Any:
        if self._compositor == CompositorKind.KDE:
            return create_kde_overlay(self.app, title=title, width=width, height=height, skin_id=skin_id)
        if self._compositor == CompositorKind.GNOME:
            return create_gnome_overlay(self.app, title=title, width=width, height=height, namespace=skin_id[:64])
        return create_overlay_window(self.app, title=title, width=width, height=height, skin_id=skin_id)

    def _notify_redraw_factory(self, runtime: SkinRuntime) -> Callable[[], None]:
        def _notify() -> None:
            def _idle() -> None:
                runtime.window.invalidate()

            GLib = gi.repository.GLib
            GLib.idle_add(_idle)

        return _notify

    def load_skin(self, skin_id: str, ini_path: Path | None = None) -> None:
        """Load a skin by id (relative path) or explicit ``.ini`` path."""
        path = ini_path or self.resolve_ini(skin_id)
        if not path or not path.is_file():
            raise FileNotFoundError(skin_id)
        skin_key = skin_id
        parsed = parse_skin_ini(path)
        layouts = compute_layout(parsed)
        w, h = _skin_bounds(layouts)
        win = self._new_overlay(title=skin_key, width=w, height=h, skin_id=skin_key)
        runtime = SkinRuntime(skin_id=skin_key, ini_path=path, parsed=parsed, window=win, layouts=layouts)

        def draw(cr: Any) -> None:
            import cairo as _cairo

            def gm(name: str) -> str:
                return self.measure_manager.get_display_value(skin_key, name.strip())

            cr.save()
            cr.set_operator(_cairo.OPERATOR_CLEAR)
            cr.paint()
            cr.restore()
            render_skin(cr, parsed, gm, layouts=layouts)

        win.set_draw_callback(draw)
        win.resize_canvas(w, h)
        win.present()
        self.skins[skin_key] = runtime
        notify = self._notify_redraw_factory(runtime)
        self.measure_manager.load_skin_measures_threadsafe(skin_key, parsed, notify)
        log.info("loaded skin %s from %s", skin_key, path)

    def unload_skin(self, skin_id: str) -> None:
        rt = self.skins.pop(skin_id, None)
        if not rt:
            return
        rt.window.close()
        if self._async_loop:
            self._async_loop.call_soon_threadsafe(lambda: self.measure_manager.clear_skin(skin_id))

    def reload_skin(self, skin_id: str) -> None:
        if skin_id not in self.skins:
            self.load_skin(skin_id)
            return
        rt = self.skins.pop(skin_id)
        rt.window.close()
        self.load_skin(skin_id, rt.ini_path)

    def resolve_ini(self, skin_id: str) -> Path | None:
        """Locate ``skin_id`` under configured roots (first match)."""
        sid = skin_id.replace("\\", "/").strip("/")
        for root in self.config.engine.skins_roots:
            base = Path(root).expanduser()
            cand = base / sid
            if cand.is_file() and cand.suffix.lower() == ".ini":
                return cand
            if (cand / "skin.ini").is_file():
                return cand / "skin.ini"
        for base in default_skins_roots():
            cand = base / sid
            if cand.is_file() and cand.suffix.lower() == ".ini":
                return cand
            if (cand / "skin.ini").is_file():
                return cand / "skin.ini"
        return None

    def list_installed_skins(self) -> list[dict[str, str]]:
        """Enumerate ``.ini`` files discovered under skin roots."""
        seen: set[str] = set()
        rows: list[dict[str, str]] = []
        roots = [Path(p).expanduser() for p in self.config.engine.skins_roots] or default_skins_roots()
        for base in roots:
            if not base.is_dir():
                continue
            for ini in base.rglob("*.ini"):
                rel = ini.relative_to(base).as_posix()
                if rel in seen:
                    continue
                seen.add(rel)
                rows.append({"id": rel, "path": str(ini.resolve())})
        rows.sort(key=lambda r: r["id"].lower())
        return rows


_ENGINE: RainTuxEngine | None = None


def get_engine() -> RainTuxEngine:
    if _ENGINE is None:
        raise RuntimeError("engine not initialized")
    return _ENGINE


def run_engine(dev_mode: bool = False) -> int:
    """Start RainTux (GTK main loop). Returns process exit code."""
    logging.basicConfig(level=logging.DEBUG if dev_mode else logging.INFO)
    cfg = load_config()
    from raintux.api.rest_api import start_api_server

    global _ENGINE

    class App(Gtk.Application):
        def __init__(self) -> None:
            super().__init__(application_id="org.raintux.RainTux", flags=Gio.ApplicationFlags.FLAGS_NONE)

        def do_activate(self) -> None:
            """Gtk.Application requires activation; overlay windows are created by the engine/API."""

    app = App()
    eng = RainTuxEngine(cfg, app)
    _ENGINE = eng

    # Bootstrap example skin path for first run
    pkg_default = Path(__file__).resolve().parent.parent / "data" / "skins" / "Minimal" / "skin.ini"
    if pkg_default.is_file():
        roots = [Path(p).expanduser() for p in cfg.engine.skins_roots]
        minimal_dir = Path.home() / "RainTux" / "Skins" / "Minimal"
        if not any((Path(r) / "Minimal" / "skin.ini").is_file() for r in roots if r.exists()):
            minimal_dir.mkdir(parents=True, exist_ok=True)
            target = minimal_dir / "skin.ini"
            if not target.exists():
                try:
                    target.write_text(pkg_default.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    log.warning("could not install default Minimal skin to %s", target)

    def on_startup(_app: Gtk.Application) -> None:
        from raintux.onboarding.first_run import begin_first_run_or_skip

        # Keep running with zero windows: skins are loaded later (API / dev auto-load),
        # and GApplication otherwise exits as soon as activate returns.
        _app.hold()

        def after_first_run(ok: bool) -> None:
            if not ok:
                _app.quit()
                return
            eng.start_background_loop()
            start_api_server(eng, cfg)
            try:
                if dev_mode:
                    ini = eng.resolve_ini("Minimal/skin.ini") or eng.resolve_ini("Minimal")
                    if ini:
                        eng.load_skin("Minimal/skin.ini", ini)
            except Exception:
                log.exception("dev auto-load failed")

        begin_first_run_or_skip(_app, after_first_run)

    app.connect("startup", on_startup)
    return app.run(None)
