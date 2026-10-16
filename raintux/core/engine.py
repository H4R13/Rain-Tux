"""GTK application: active skins, their windows and timers, bangs that act on the desktop."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from raintux.core.bangs import split_args, split_commands
from raintux.core.config import RainTuxConfig, default_skins_roots, load_config
from raintux.core.formula import parse_number
from raintux.core.skin import Skin
from raintux.core.state import AppState, load_state, save_state
from raintux.wayland.compositor_detect import CompositorKind, detect_compositor
from raintux.wayland.layer_window import CaptureOverlay, SkinWindow, monitor_at, monitors

log = logging.getLogger(__name__)

_T = TypeVar("_T")
DEFAULT_LAYOUT = [  # first launch: (config, file, column); the right column stacks top to bottom
    (r"RainTux\Welcome", "Welcome.ini", "center"),
    (r"RainTux\Clock", "Clock.ini", "right"),
    (r"RainTux\System", "System.ini", "right"),
    (r"RainTux\Network", "Network.ini", "right"),
]


def _on_main_thread(fn: Callable[..., _T], *args: Any, timeout: float = 30.0) -> _T:
    """Run ``fn`` on the GTK main loop and wait for its result (direct call if already there)."""
    if GLib.MainContext.default().is_owner():
        return fn(*args)
    done = threading.Event()
    box: dict[str, Any] = {}

    def _run() -> bool:
        try:
            box["result"] = fn(*args)
        except BaseException as e:  # re-raised in the calling thread
            box["error"] = e
        finally:
            done.set()
        return GLib.SOURCE_REMOVE

    GLib.idle_add(_run)
    if not done.wait(timeout):
        raise TimeoutError(f"GTK main loop did not run {getattr(fn, '__name__', fn)}")
    if "error" in box:
        raise box["error"]
    return box["result"]


class ActiveSkin:
    """Bookkeeping for one active config (API-compatible: ``ini_path``)."""

    def __init__(self, skin: Skin, window: SkinWindow) -> None:
        self.skin = skin
        self.window = window
        self.timer = 0

    @property
    def ini_path(self) -> Path:
        return self.skin.ini_path


class RainTuxEngine:
    """Owns every active skin and implements :class:`~raintux.core.skin.SkinHost`."""

    def __init__(self, config: RainTuxConfig, app: Gtk.Application) -> None:
        self.config = config
        self.app = app
        self.state: AppState = load_state()
        self.active: dict[str, ActiveSkin] = {}  # lower-case config -> skin
        self._compositor = detect_compositor()
        self._manage_window: Any = None
        self._save_source = 0

    @property
    def compositor(self) -> CompositorKind:
        return self._compositor

    # ------------------------------------------------------------------ skin discovery
    def roots(self) -> list[Path]:
        roots = [Path(r).expanduser() for r in self.config.engine.skins_roots]
        return roots + [b for b in default_skins_roots() if b not in roots]

    def primary_root(self) -> Path:
        root = self.roots()[0]
        root.mkdir(parents=True, exist_ok=True)
        return root

    def find_config_dir(self, config: str) -> tuple[Path, Path] | None:
        """(skins root, config folder) for ``config`` like ``illustro\\Clock``."""
        rel = config.replace("\\", "/").strip("/")
        if not rel:
            return None
        for base in self.roots():
            try:
                base_r = base.resolve()
            except OSError:
                continue
            cand = (base / rel).resolve()
            if cand.is_relative_to(base_r) and cand.is_dir():
                return base_r, cand
        return None

    def list_configs(self) -> dict[str, list[str]]:
        """``{config: [ini files]}`` for every folder with skins (``@Resources`` skipped)."""
        out: dict[str, list[str]] = {}
        for base in self.roots():
            if not base.is_dir():
                continue
            for ini in sorted(base.rglob("*.ini")):
                rel = ini.relative_to(base)
                if any(p.startswith("@") for p in rel.parts[:-1]) or len(rel.parts) < 2:
                    continue
                config = "\\".join(rel.parts[:-1])
                if config.lower() in (c.lower() for c in out) and config not in out:
                    continue
                out.setdefault(config, []).append(ini.name)
        return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))

    def list_installed_skins(self) -> list[dict[str, str]]:
        rows = []
        for config, files in self.list_configs().items():
            found = self.find_config_dir(config)
            for f in files:
                rows.append({"id": config.replace("\\", "/") + "/" + f, "path": str(found[1] / f) if found else ""})
        return rows

    def resolve_ini(self, skin_id: str) -> Path | None:
        """``Config/File.ini`` (or a folder with one .ini) under the skin roots; never outside them."""
        sid = skin_id.replace("\\", "/").strip("/")
        for base in self.roots():
            try:
                base_r = base.resolve()
            except OSError:
                continue
            cand = (base / sid).resolve()
            if not cand.is_relative_to(base_r):
                log.warning("rejected skin id outside %s: %r", base, skin_id)
                continue
            if cand.is_file() and cand.suffix.lower() == ".ini":
                return cand
            if cand.is_dir():
                inis = sorted(cand.glob("*.ini"))
                if inis:
                    return inis[0]
        return None

    def _split_id(self, ini: Path) -> tuple[str, Path]:
        for base in self.roots():
            try:
                rel = ini.resolve().relative_to(base.resolve())
            except (ValueError, OSError):
                continue
            return "\\".join(rel.parts[:-1]), base.resolve()
        return ini.parent.name, ini.parent.parent

    # ------------------------------------------------------------------ activation
    def activate_config(self, config: str, file: str | None = None) -> Skin:
        found = self.find_config_dir(config)
        if found is None:
            raise FileNotFoundError(config)
        root, folder = found
        if file is None:
            cs = self.state.config(config)
            file = cs.active_file or (sorted(p.name for p in folder.glob("*.ini")) or [None])[0]
        if file is None:
            raise FileNotFoundError(config)
        ini = (folder / file).resolve()
        if not ini.is_relative_to(folder) or not ini.is_file():
            raise FileNotFoundError(f"{config}\\{file}")
        return self._activate(ini, root)

    def _activate(self, ini: Path, root: Path) -> Skin:
        config, _ = self._split_id(ini)
        key = config.lower()
        cs = self.state.config(config)
        existing = self.active.get(key)
        if existing is not None:
            self._stop(existing)
        skin = Skin(ini, config=config, host=self, skins_root=root, state=cs.window)
        try:
            skin.load()
            self._register_fonts(skin)
        except Exception:
            log.exception("could not load %s", ini)
            if existing is not None:
                existing.window.destroy()
                self.active.pop(key, None)
            raise
        if existing is not None:
            window = existing.window
            window.set_skin(skin)
        else:
            window = SkinWindow(self, skin)
        act = ActiveSkin(skin, window)
        self.active[key] = act
        cs.active_file = ini.name
        skin.start()
        window.apply_state()
        window.present()
        if existing is None and cs.window.fade_duration > 0 and cs.window.alpha > 0:
            window.set_opacity(0.0)
            window.fade_to(cs.window.alpha / 255.0, cs.window.fade_duration)
        self._schedule(act)
        self.schedule_save()
        log.info("activated %s (%s)", config, ini.name)
        return skin

    def _schedule(self, act: ActiveSkin) -> None:
        if act.timer:
            GLib.source_remove(act.timer)
            act.timer = 0
        ms = act.skin.update_ms
        if ms <= 0:
            return
        interval = max(16, int(ms))

        def tick() -> bool:
            if act.skin.closed:
                act.timer = 0
                return GLib.SOURCE_REMOVE
            act.skin.update()
            if act.skin.update_ms != interval:
                self._schedule(act)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        act.timer = GLib.timeout_add(interval, tick)

    def _stop(self, act: ActiveSkin) -> None:
        if act.timer:
            GLib.source_remove(act.timer)
            act.timer = 0
        try:
            act.skin.close()
        except Exception:
            log.exception("closing %s", act.skin.config)

    def deactivate_config(self, config: str) -> None:
        act = self.active.pop(config.replace("/", "\\").lower(), None)
        if act is None:
            return
        self._stop(act)
        self.state.config(act.skin.config).active_file = None
        fade = act.skin.state.fade_duration
        act.window.fade_to(0.0, fade, then=act.window.destroy)
        self.schedule_save()
        if self._manage_window is not None:
            self._manage_window.refresh()

    def refresh_config(self, config: str) -> None:
        act = self.active.get(config.replace("/", "\\").lower())
        if act is None:
            return
        self._activate(act.skin.ini_path, act.skin.skins_root)

    def refresh_all(self) -> None:
        for act in list(self.active.values()):
            self.refresh_config(act.skin.config)

    # API compatibility (called from FastAPI worker threads).
    @property
    def skins(self) -> dict[str, ActiveSkin]:
        out = {}
        for act in self.active.values():
            out[act.skin.config.replace("\\", "/") + "/" + act.skin.ini_path.name] = act
        return out

    def load_skin(self, skin_id: str, ini_path: Path | None = None) -> None:
        def go() -> None:
            ini = ini_path or self.resolve_ini(skin_id)
            if ini is None or not ini.is_file():
                raise FileNotFoundError(skin_id)
            _config, root = self._split_id(ini)
            self._activate(ini, root)

        _on_main_thread(go)

    def unload_skin(self, skin_id: str) -> None:
        def go() -> None:
            ini = self.resolve_ini(skin_id)
            config = self._split_id(ini)[0] if ini else skin_id.replace("/", "\\").rsplit("\\", 1)[0]
            self.deactivate_config(config)

        _on_main_thread(go)

    def reload_skin(self, skin_id: str) -> None:
        def go() -> None:
            ini = self.resolve_ini(skin_id)
            if ini is None:
                raise FileNotFoundError(skin_id)
            config, root = self._split_id(ini)
            if config.lower() in self.active:
                self.refresh_config(config)
            else:
                self._activate(ini, root)

        _on_main_thread(go)

    def apply_window_settings(self, skin_id: str, x: int | None, y: int | None, alpha: float | None,
                              click_through: bool | None) -> bool:
        def go() -> bool:
            ini = self.resolve_ini(skin_id)
            act = self.active.get(self._split_id(ini)[0].lower()) if ini else None
            if act is None:
                return False
            st = act.skin.state
            if x is not None or y is not None:
                act.window.move_to(st.x if x is None else x, st.y if y is None else y)
            if alpha is not None:
                st.alpha = int(max(0.0, min(1.0, alpha)) * 255)
            if click_through is not None:
                st.click_through = click_through
            act.window.apply_state()
            self.schedule_save()
            return True

        return _on_main_thread(go)

    # ------------------------------------------------------------------ startup / shutdown
    def start(self) -> None:
        install_default_skins(self.primary_root())
        active = self.state.active()
        if not active and not self.state.first_run_done:
            self._activate_default_layout()
        else:
            for config, cs in active:
                try:
                    self.activate_config(config, cs.active_file)
                except Exception:
                    log.exception("could not activate %s", config)
        self.state.first_run_done = True
        self.schedule_save()

    def _activate_default_layout(self) -> None:
        mons = monitors()
        g = mons[0].get_geometry() if mons else None
        sw, sh = (g.width, g.height) if g else (1920, 1080)
        ox, oy = (g.x, g.y) if g else (0, 0)
        next_y = oy + 40
        for config, file, column in DEFAULT_LAYOUT:
            if self.find_config_dir(config) is None:
                continue
            try:
                skin = self.activate_config(config, file)
            except Exception:
                log.exception("default skin %s", config)
                continue
            act = self.active[config.lower()]
            if column == "center":
                act.window.move_to(ox + (sw - skin.width) // 2, oy + (sh - skin.height) // 2)
            else:
                act.window.move_to(ox + sw - skin.width - 40, next_y)
                next_y += skin.height + 16

    def shutdown(self) -> None:
        for act in list(self.active.values()):
            self._stop(act)
        self.save_now()

    def schedule_save(self) -> None:
        if self._save_source:
            return

        def go() -> bool:
            self._save_source = 0
            self.save_now()
            return GLib.SOURCE_REMOVE

        self._save_source = GLib.timeout_add(500, go)

    def save_now(self) -> None:
        try:
            save_state(self.state)
        except OSError:
            log.exception("could not save skin state")

    # ------------------------------------------------------------------ fonts
    def _register_fonts(self, skin: Skin) -> None:
        from raintux.gui.fonts import register_skin_fonts

        try:
            register_skin_fonts(skin)
        except Exception:
            log.debug("font registration failed", exc_info=True)

    # ------------------------------------------------------------------ SkinHost
    def _act(self, skin: Skin) -> ActiveSkin | None:
        act = self.active.get(skin.config.lower())
        return act if act is not None and act.skin is skin else None

    def redraw(self, skin: Skin) -> None:
        act = self._act(skin)
        if act is not None:
            act.window.redraw()

    def resize(self, skin: Skin, width: int, height: int) -> None:
        act = self._act(skin)
        if act is not None:
            act.window.resize_canvas(width, height)

    def call_later(self, ms: int, fn: Callable[[], None]) -> Any:
        def run() -> bool:
            try:
                fn()
            except Exception:
                log.exception("timer callback failed")
            return GLib.SOURCE_REMOVE

        return GLib.timeout_add(max(0, int(ms)), run)

    def cancel(self, handle: Any) -> None:
        if handle:
            try:
                GLib.source_remove(handle)
            except Exception:
                pass

    def call_soon(self, fn: Callable[[], None]) -> None:
        def run() -> bool:
            try:
                fn()
            except Exception:
                log.exception("deferred callback failed")
            return GLib.SOURCE_REMOVE

        GLib.idle_add(run)

    def find_skin(self, config: str) -> Skin | None:
        act = self.active.get(config.replace("/", "\\").strip().lower())
        return act.skin if act else None

    def screen_variables(self, skin: Skin) -> dict[str, str]:
        mons = monitors()
        out: dict[str, str] = {}
        if not mons:
            return out
        geos = [m.get_geometry() for m in mons]
        cur = monitor_at(skin.state.x + 1, skin.state.y + 1)
        cg = cur.get_geometry() if cur is not None else geos[0]

        def put(prefix: str, g: Any, suffix: str = "") -> None:
            out[f"{prefix}X{suffix}"] = str(g.x)
            out[f"{prefix}Y{suffix}"] = str(g.y)
            out[f"{prefix}WIDTH{suffix}"] = str(g.width)
            out[f"{prefix}HEIGHT{suffix}"] = str(g.height)

        for prefix in ("SCREENAREA", "WORKAREA"):
            put(prefix, cg)
            put("P" + prefix, geos[0])
            for i, g in enumerate(geos, start=1):
                put(prefix, g, f"@{i}")
        vx = min(g.x for g in geos)
        vy = min(g.y for g in geos)
        vw = max(g.x + g.width for g in geos) - vx
        vh = max(g.y + g.height for g in geos) - vy
        for prefix in ("VSCREENAREA",):
            out.update({f"{prefix}X": str(vx), f"{prefix}Y": str(vy), f"{prefix}WIDTH": str(vw), f"{prefix}HEIGHT": str(vh)})
        return out

    def set_pointer(self, skin: Skin, cursor: str | None, tooltip: str | None) -> None:
        act = self._act(skin)
        if act is None:
            return
        area = act.window.area
        area.set_cursor_from_name(cursor) if cursor else area.set_cursor(None)
        if (area.get_tooltip_text() or None) != tooltip:
            area.set_tooltip_text(tooltip)

    def all_skins(self) -> list[Skin]:
        return [a.skin for a in self.active.values()]

    def set_keyboard_focus(self, skin: Skin, want: bool) -> None:
        act = self._act(skin)
        if act is not None:
            act.window.set_keyboard(want)

    def clipboard_set(self, text: str) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)

    def clipboard_get(self, callback: Callable[[str], Any]) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        cb = display.get_clipboard()

        def done(c: Any, res: Gio.AsyncResult) -> None:
            try:
                text = c.read_text_finish(res)
            except GLib.Error:
                text = None
            if text:
                callback(text)

        cb.read_text_async(None, done)

    def start_mouse_capture(self, skin: Skin, measure: Any) -> None:
        self._captures = getattr(self, "_captures", {})
        self._captures[id(measure)] = CaptureOverlay(self.app, skin, measure)

    def stop_mouse_capture(self, skin: Skin, measure: Any) -> None:
        ov = getattr(self, "_captures", {}).pop(id(measure), None)
        if ov is not None:
            ov.destroy()

    def ask_text(self, skin: Skin, rect: tuple[float, float, float, float], style: dict[str, Any], default: str,
                 password: bool, callback: Callable[[str | None], None]) -> None:
        from raintux.gui.input_popup import ask_text

        ask_text(self.app, skin, rect, style, default, password, callback)

    def run_command(self, skin: Skin, command: str) -> None:
        """Run a non-bang action: URLs and files open with the default app, programs start detached."""
        args = split_args(command)
        if not args:
            return
        prog = args[0].strip()
        low = prog.lower()
        try:
            if re.match(r"^[a-z][a-z0-9+.\-]*:(//)?", low) and not re.match(r"^[a-z]:[\\/]", low):
                Gio.AppInfo.launch_default_for_uri(prog, None)
                return
            if low in ("explorer", "explorer.exe"):
                target = skin.path(args[1]) if len(args) > 1 else Path.home()
                Gio.AppInfo.launch_default_for_uri(target.as_uri(), None)
                return
            if low in ("notepad", "notepad.exe") and len(args) > 1:
                self.open_in_editor(skin.path(args[1]))
                return
            path = skin.path(prog)
            if path.exists() and not (path.is_file() and os.access(path, os.X_OK)):
                Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
                return
            exe = shutil.which(prog) or (str(path) if path.is_file() and os.access(path, os.X_OK) else None)
            if exe is None:
                log.warning("[%s] cannot run %r (not found on this system)", skin.config, prog)
                return
            subprocess.Popen([exe, *args[1:]], cwd=str(skin.root), start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            log.exception("[%s] command failed: %s", skin.config, command)

    def open_in_editor(self, path: Path) -> None:
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if editor and shutil.which(editor.split()[0]):
            terminal_editors = {"vi", "vim", "nvim", "nano", "micro", "emacs -nw", "helix", "hx"}
            if editor in terminal_editors:
                term = shutil.which("x-terminal-emulator") or shutil.which("foot") or shutil.which("kitty") or shutil.which("alacritty")
                if term:
                    subprocess.Popen([term, "-e", *editor.split(), str(path)], start_new_session=True)
                    return
            else:
                subprocess.Popen([*editor.split(), str(path)], start_new_session=True)
                return
        Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)

    def host_bang(self, skin: Skin, name: str, args: list[str]) -> None:
        """Bangs that act on windows, configs or the app (``!Refresh``, ``!Move``, ``!ActivateConfig``…)."""

        def target(idx: int) -> list[ActiveSkin]:
            if len(args) > idx and args[idx].strip():
                cfg = args[idx].strip()
                if cfg == "*":
                    return list(self.active.values())
                a = self.active.get(cfg.replace("/", "\\").lower())
                return [a] if a else []
            a = self._act(skin)
            return [a] if a else []

        def flag(value: str, current: bool) -> bool:
            v = int(parse_number(value, 0)) if value.strip() else 1
            return (not current) if v == -1 else bool(v)

        if name in ("refresh",):
            for a in target(0):
                cfg = a.skin.config
                GLib.idle_add(lambda c=cfg: (self.refresh_config(c), GLib.SOURCE_REMOVE)[1])
        elif name == "refreshapp":
            GLib.idle_add(lambda: (self.refresh_all(), GLib.SOURCE_REMOVE)[1])
        elif name == "refreshgroup" and args:
            for a in self._group(args[0]):
                GLib.idle_add(lambda c=a.skin.config: (self.refresh_config(c), GLib.SOURCE_REMOVE)[1])
        elif name in ("move", "setwindowposition") and len(args) >= 2:
            for a in target(2 if name == "move" else 4):
                x = int(parse_number(args[0], a.skin.state.x))
                y = int(parse_number(args[1], a.skin.state.y))
                a.window.move_to(x, y)
                self.window_moved(a.window)
        elif name in ("draggable", "keeponscreen", "clickthrough", "snapedges") and args:
            attr = {"draggable": "draggable", "keeponscreen": "keep_on_screen", "clickthrough": "click_through",
                    "snapedges": "snap_edges"}[name]
            for a in target(1):
                setattr(a.skin.state, attr, flag(args[0], getattr(a.skin.state, attr)))
                a.window.apply_state()
            self.schedule_save()
        elif name == "zpos" and args:
            for a in target(1):
                a.skin.state.z = max(-2, min(2, int(parse_number(args[0], 0))))
                a.window.apply_state()
            self.schedule_save()
        elif name == "settransparency" and args:
            for a in target(1):
                a.skin.state.alpha = max(0, min(255, int(parse_number(args[0], 255))))
                a.window.apply_state()
            self.schedule_save()
        elif name == "fadeduration" and args:
            for a in target(1):
                a.skin.state.fade_duration = max(0, int(parse_number(args[0], 250)))
        elif name in ("show", "hide", "toggle", "showfade", "hidefade", "togglefade"):
            for a in target(0):
                fade = a.skin.state.fade_duration if name.endswith("fade") else 0
                visible = a.window.get_opacity() > 0.01 and a.window.get_visible()
                show = name.startswith("show") or (name.startswith("toggle") and not visible)
                if show:
                    a.window.set_visible(True)
                    a.window.fade_to(a.skin.state.alpha / 255.0, fade)
                else:
                    a.window.fade_to(0.0, fade)
        elif name in ("activateconfig", "toggleconfig") and args:
            cfg = args[0]
            file = args[1] if len(args) > 1 else None
            key = cfg.replace("/", "\\").lower()
            if name == "toggleconfig" and key in self.active and (file is None or self.active[key].skin.ini_path.name.lower() == file.lower()):
                GLib.idle_add(lambda: (self.deactivate_config(cfg), GLib.SOURCE_REMOVE)[1])
            else:
                GLib.idle_add(lambda: (self._safe_activate(cfg, file), GLib.SOURCE_REMOVE)[1])
        elif name == "deactivateconfig":
            for a in target(0):
                GLib.idle_add(lambda c=a.skin.config: (self.deactivate_config(c), GLib.SOURCE_REMOVE)[1])
        elif name == "deactivateconfiggroup" and args:
            for a in self._group(args[0]):
                GLib.idle_add(lambda c=a.skin.config: (self.deactivate_config(c), GLib.SOURCE_REMOVE)[1])
        elif name in ("manage", "about"):
            self.show_manage(skin.config if name == "manage" and not args else (args[1] if len(args) > 1 else None))
        elif name == "skinmenu":
            for a in target(0):
                self.show_context_menu(a.window, 10, 10)
        elif name == "traymenu":
            self.show_manage()
        elif name == "quit":
            GLib.idle_add(lambda: (self.app.quit(), GLib.SOURCE_REMOVE)[1])
        elif name in ("edit", "editskin"):
            for a in target(0):
                self.open_in_editor(a.skin.ini_path)
        elif name == "setclip" and args:
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(args[0])
        elif name == "setwallpaper" and args:
            set_wallpaper(skin.path(args[0]))
        elif name in ("showgroup", "hidegroup", "togglegroup", "showfadegroup", "hidefadegroup", "togglefadegroup") and args:
            for a in self._group(args[0]):
                self.host_bang(a.skin, name[:-5], [])
        elif name in ("updategroup", "redrawgroup") and args:
            for a in self._group(args[0]):
                a.skin.bang(name[:-5], [])
        elif name in ("disablemouseactionskingroup", "enablemouseactionskingroup", "togglemouseactionskingroup",
                      "clearmouseactionskingroup") and len(args) >= 2:
            local = name.replace("skingroup", "")
            for a in self._group(args[1]):
                a.skin.bang(local, ["*", args[0]])
        elif name == "setzoomfactor" and args:
            for a in target(1):
                raw = args[0].strip()
                cur = a.window.zoom
                val = parse_number(raw.lstrip("+"), cur)
                a.window.set_zoom(cur + val if raw.startswith(("+", "-")) else val)
        elif name == "skincustommenu":
            for a in target(0):
                from raintux.gui.context_menu import show_custom_menu

                show_custom_menu(self, a.window, 10, 10)
        elif name == "restart":
            GLib.idle_add(lambda: (self._restart(), GLib.SOURCE_REMOVE)[1])
        elif name == "debug":
            self.show_manage()
        elif name == "sendkey" and args:
            from raintux.measures.plugins.media import MediaKeyPlugin

            keymap = {"medianexttrack": "nexttrack", "mediaprevtrack": "prevtrack", "mediastop": "stop",
                      "mediaplaypause": "playpause", "volumemute": "volumemute", "volumedown": "volumedown",
                      "volumeup": "volumeup"}
            key = keymap.get(args[0].strip().lower())
            if key:
                MediaKeyPlugin.command(MediaKeyPlugin.__new__(MediaKeyPlugin), key)
        elif name in ("autoselectscreen", "autoselectscreengroup", "forceenablevisiblemode", "lsboxhook",
                      "showblur", "hideblur", "toggleblur", "addblur", "removeblur", "resetstats", "setanchor"):
            log.debug("!%s has no effect on Linux", name)
        else:
            log.info("!%s is not supported on this platform", name)

    def _restart(self) -> None:
        import sys

        self.shutdown()
        os.execv(sys.executable, [sys.executable, "-m", "raintux"])

    def _safe_activate(self, config: str, file: str | None) -> None:
        try:
            self.activate_config(config, file)
        except FileNotFoundError:
            log.warning("!ActivateConfig: %s %s not found", config, file or "")
        if self._manage_window is not None:
            self._manage_window.refresh()

    def _group(self, group: str) -> list[ActiveSkin]:
        g = group.strip().lower()
        out = []
        for a in self.active.values():
            groups = {x.strip().lower() for x in a.skin.rm.get("group").split("|") if x.strip()}
            if g in groups:
                out.append(a)
        return out

    def run_bang_threadsafe(self, action: str) -> None:
        _on_main_thread(self.execute_global, action)

    def execute_global(self, action: str) -> None:
        """Bangs from the command line / tray: run against the named config or the first skin."""
        for cmd in split_commands(action) or [action]:
            args = split_args(cmd.lstrip("!"))
            if not args:
                continue
            name = args[0].lower()
            if name.startswith("rainmeter"):
                name = name[len("rainmeter"):]
            if name in ("refreshapp", "quit", "manage", "about", "activateconfig", "toggleconfig",
                        "deactivateconfiggroup", "refreshgroup", "loadlayout"):
                anchor = next(iter(self.active.values()), None)
                if name == "manage":
                    self.show_manage(args[1] if len(args) > 1 else None)
                elif anchor is not None:
                    self.host_bang(anchor.skin, name, args[1:])
                elif name in ("activateconfig", "toggleconfig") and len(args) > 1:
                    self._safe_activate(args[1], args[2] if len(args) > 2 else None)
                elif name == "quit":
                    self.app.quit()
                continue
            # Other bangs carry the target config as their last argument.
            for a in list(self.active.values()):
                if len(args) > 1 and args[-1].replace("/", "\\").lower() == a.skin.config.lower():
                    a.skin.execute("[" + cmd + "]")
                    break
            else:
                if len(self.active) == 1:
                    next(iter(self.active.values())).skin.execute("[" + cmd + "]")
                else:
                    log.warning("bang %s: name the target config as the last argument", cmd)

    # ------------------------------------------------------------------ windows
    def show_context_menu(self, window: SkinWindow, x: float, y: float) -> None:
        from raintux.gui.context_menu import show_context_menu

        show_context_menu(self, window, x, y)

    def show_manage(self, config: str | None = None) -> None:
        from raintux.gui.manage import ManageWindow

        if self._manage_window is None:
            self._manage_window = ManageWindow(self)
            self._manage_window.connect("destroy", lambda *_: setattr(self, "_manage_window", None))
        self._manage_window.refresh(select=config)
        self._manage_window.present()

    def other_rects(self, window: SkinWindow) -> list[tuple[int, int, int, int]]:
        return [(a.skin.state.x, a.skin.state.y, a.skin.width, a.skin.height)
                for a in self.active.values() if a.window is not window]

    def snap_position(self, window: SkinWindow, x: int, y: int) -> tuple[int, int]:
        w, h = window.skin.width, window.skin.height
        xs = []
        ys = []
        mon = monitor_at(x + w / 2, y + h / 2)
        if mon is not None:
            g = mon.get_geometry()
            xs += [g.x, g.x + g.width - w]
            ys += [g.y, g.y + g.height - h]
        for ox, oy, ow, oh in self.other_rects(window):
            xs += [ox, ox + ow, ox - w, ox + ow - w]
            ys += [oy, oy + oh, oy - h, oy + oh - h]
        bx = min(xs, key=lambda v: abs(v - x), default=x)
        by = min(ys, key=lambda v: abs(v - y), default=y)
        return (bx if abs(bx - x) <= 10 else x, by if abs(by - y) <= 10 else y)

    def clamp_on_screen(self, window: SkinWindow, x: int, y: int) -> tuple[int, int]:
        w, h = window.skin.width, window.skin.height
        mon = monitor_at(x + w / 2, y + h / 2)
        if mon is None:
            return (x, y)
        g = mon.get_geometry()
        x = max(g.x, min(x, g.x + g.width - w))
        y = max(g.y, min(y, g.y + g.height - h))
        return (x, y)

    def window_moved(self, window: SkinWindow) -> None:
        window.skin.invalidate_builtins()
        if window.skin.state.save_position:
            self.schedule_save()


def install_default_skins(root: Path) -> None:
    """Copy bundled skins into the user's skins folder (never overwriting user files)."""
    src = Path(__file__).resolve().parent.parent / "data" / "skins"
    if not src.is_dir():
        return
    for item in src.iterdir():
        dest = root / item.name
        if dest.exists():
            continue
        try:
            shutil.copytree(item, dest) if item.is_dir() else shutil.copy2(item, dest)
            log.info("installed default skins: %s", dest)
        except OSError:
            log.warning("could not install %s", dest, exc_info=True)


def set_wallpaper(path: Path) -> None:
    """Best-effort wallpaper change across desktops (GNOME, KDE, sway/Hyprland via swaybg)."""
    uri = path.resolve().as_uri()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    try:
        if "gnome" in desktop or "unity" in desktop or "cinnamon" in desktop:
            for key in ("picture-uri", "picture-uri-dark"):
                subprocess.run(["gsettings", "set", "org.gnome.desktop.background", key, uri], check=False)
        elif "kde" in desktop and shutil.which("plasma-apply-wallpaperimage"):
            subprocess.run(["plasma-apply-wallpaperimage", str(path)], check=False)
        elif os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
            subprocess.run(["swaymsg", "output", "*", "bg", str(path), "fill"], check=False)
        elif shutil.which("swww"):
            subprocess.run(["swww", "img", str(path)], check=False)
        elif shutil.which("swaybg"):
            subprocess.Popen(["swaybg", "-i", str(path), "-m", "fill"], start_new_session=True)
        else:
            log.info("!SetWallpaper: no supported wallpaper tool found")
    except OSError:
        log.exception("!SetWallpaper failed")


_ENGINE: RainTuxEngine | None = None


def get_engine() -> RainTuxEngine:
    if _ENGINE is None:
        raise RuntimeError("engine not initialized")
    return _ENGINE


def run_engine(dev_mode: bool = False, argv: list[str] | None = None) -> int:
    """Start RainTux (GTK main loop), or forward the command line to the running instance."""
    logging.basicConfig(level=logging.DEBUG if dev_mode else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    from raintux.api.rest_api import start_api_server

    global _ENGINE

    class App(Gtk.Application):
        def __init__(self) -> None:
            super().__init__(application_id="org.raintux.RainTux", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.started = False

        def do_command_line(self, cmdline: Gio.ApplicationCommandLine) -> int:
            args = list(cmdline.get_arguments() or [])[1:]
            if not self.started:
                self.started = True
                self.activate()
                if args:
                    GLib.timeout_add(1500, lambda: (handle_args(args), GLib.SOURCE_REMOVE)[1])
                return 0
            if not args:
                eng.show_manage()
            else:
                handle_args(args)
            return 0

        def do_activate(self) -> None:
            pass

    def handle_args(args: list[str]) -> None:
        if not args:
            return
        if args[0] in ("--manage", "manage"):
            eng.show_manage(args[1] if len(args) > 1 else None)
        elif args[0] in ("--refresh-all", "refresh"):
            eng.refresh_all()
        elif args[0] in ("install",) and len(args) > 1:
            from raintux.gui.installer import install_dialog

            install_dialog(eng, Path(args[1]))
        elif args[0].lower().endswith(".rmskin"):
            from raintux.gui.installer import install_dialog

            install_dialog(eng, Path(args[0]))
        elif args[0].startswith("!") or args[0].startswith("["):
            eng.execute_global(" ".join(_quote(a) for a in args))
        else:
            log.warning("unknown arguments: %s", args)

    app = App()
    eng = RainTuxEngine(cfg, app)
    _ENGINE = eng

    def on_startup(_app: Gtk.Application) -> None:
        from raintux.onboarding.first_run import begin_first_run_or_skip

        # Keep running with zero windows; GApplication would exit after activate otherwise.
        _app.hold()

        def after_first_run(ok: bool) -> None:
            if not ok:
                _app.quit()
                return
            try:
                start_api_server(eng, cfg)
            except Exception:
                log.exception("API server failed to start")
            eng.start()

        begin_first_run_or_skip(_app, after_first_run)

    app.connect("startup", on_startup)
    app.connect("shutdown", lambda *_: eng.shutdown())
    import sys

    return app.run(argv if argv is not None else sys.argv)


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg and not arg.startswith(("[", "!"))) else arg
