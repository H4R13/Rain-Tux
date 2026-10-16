"""First-run splash (optional GIF) and terms acknowledgment — GTK4 only."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from raintux.gui.compat import load_css

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

MARKER_REL = Path(".config") / "raintux" / "first_run_ok"

DEFAULT_EULA = """RainTux — acknowledgment

RainTux is provided "as is", without warranty. By continuing you agree that you use this software at your own risk and that the authors are not liable for any damage or data loss.

Third-party skins and libraries may have their own licenses.

See the LICENSE file in the RainTux repository for the project license.
"""


def marker_path() -> Path:
    return Path.home() / MARKER_REL


def should_show_first_run() -> bool:
    if os.environ.get("RAINTUX_SKIP_FIRST_RUN", "").lower() in ("1", "true", "yes"):
        return False
    if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        return False
    return not marker_path().is_file()


def mark_first_run_complete() -> None:
    p = marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()


def _repo_root() -> Path:
    """``Voidmeter Tux/`` (parent of the ``raintux`` package)."""
    return Path(__file__).resolve().parent.parent.parent


def _path_from_env_value(raw: str) -> Path | None:
    """Resolve ``RAINTUX_SPLASH_GIF``: absolute, cwd-relative, or repo-relative."""
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.is_absolute() and p.is_file():
        return p.resolve()
    if p.is_file():
        return p.resolve()
    rel = _repo_root() / raw.replace("\\", "/")
    if rel.is_file():
        return rel.resolve()
    return None


def resolve_splash_gif() -> Path | None:
    env = os.environ.get("RAINTUX_SPLASH_GIF", "").strip()
    if env:
        got = _path_from_env_value(env)
        if got is not None:
            return got
    root_gif = _repo_root() / "loading_bg.gif"
    if root_gif.is_file():
        return root_gif.resolve()
    legacy = _repo_root() / "raintux installer temp" / "loading_bg.gif"
    if legacy.is_file():
        return legacy
    pkg = Path(__file__).resolve().parent.parent / "data" / "assets" / "splash.gif"
    if pkg.is_file():
        return pkg
    return None


def _splash_seconds() -> float:
    """Fallback duration when no GIF or GIF timing cannot be read."""
    try:
        return max(0.5, float(os.environ.get("RAINTUX_SPLASH_SECONDS", "2.5")))
    except ValueError:
        return 2.5


def gif_one_loop_duration_ms(path: Path) -> int | None:
    """Sum frame ``duration`` fields for one full loop (PIL); ``None`` if unreadable."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
            if not isinstance(n, int) or n < 1:
                n = 1
            total = 0
            for i in range(n):
                im.seek(i)
                total += int(im.info.get("duration", 100))
            return max(total, 100)
    except Exception:
        log.warning("Could not read GIF frame timing: %s", path, exc_info=True)
        return None


def splash_duration_seconds(path: Path | None, anim: GdkPixbuf.PixbufAnimation | None) -> float:
    """Wall-clock length for exactly one GIF loop, or fallback seconds."""
    if path and path.is_file() and anim and not anim.is_static_image():
        ms = gif_one_loop_duration_ms(path)
        if ms is not None:
            return max(0.2, ms / 1000.0)
    return _splash_seconds()


def _show_eula_dialog(app: Gtk.Application, on_done: Callable[[bool], None]) -> None:
    win = Gtk.ApplicationWindow(application=app)
    win.set_title("RainTux — terms")
    win.set_default_size(560, 420)
    win.set_modal(True)

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    outer.set_margin_top(16)
    outer.set_margin_bottom(16)
    outer.set_margin_start(16)
    outer.set_margin_end(16)
    win.set_child(outer)

    title = Gtk.Label(label="Please read before continuing")
    title.add_css_class("title-2")
    outer.append(title)

    scroll = Gtk.ScrolledWindow()
    scroll.set_vexpand(True)
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    tv = Gtk.TextView()
    tv.set_editable(False)
    tv.set_cursor_visible(False)
    tv.set_wrap_mode(Gtk.WrapMode.WORD)
    buf = tv.get_buffer()
    buf.set_text(os.environ.get("RAINTUX_EULA_TEXT", DEFAULT_EULA).strip())
    scroll.set_child(tv)
    outer.append(scroll)

    agree = Gtk.CheckButton(label="I have read and agree to continue")

    def accept() -> None:
        mark_first_run_complete()
        win.destroy()
        GLib.idle_add(lambda: on_done(True))

    def decline() -> None:
        win.destroy()
        GLib.idle_add(lambda: on_done(False))

    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btn_row.set_halign(Gtk.Align.END)

    quit_btn = Gtk.Button(label="Quit")
    quit_btn.connect("clicked", lambda *_: decline())

    cont_btn = Gtk.Button(label="Continue")
    cont_btn.add_css_class("suggested-action")
    cont_btn.set_sensitive(False)

    def sync_cont() -> None:
        cont_btn.set_sensitive(agree.get_active())

    agree.connect("toggled", lambda *_: sync_cont())
    cont_btn.connect("clicked", lambda *_: accept() if agree.get_active() else None)

    btn_row.append(quit_btn)
    btn_row.append(cont_btn)
    outer.append(agree)
    outer.append(btn_row)

    win.connect("close-request", lambda *_: decline())
    win.present()


def begin_first_run_or_skip(app: Gtk.Application, on_done: Callable[[bool], None]) -> None:
    """
    Run splash + terms on first launch; otherwise ``on_done(True)`` immediately.

    ``on_done`` is always scheduled on the main loop via ``idle_add``.
    """
    if not should_show_first_run():
        GLib.idle_add(lambda: on_done(True))
        return

    splash_path = resolve_splash_gif()
    anim_pre: GdkPixbuf.PixbufAnimation | None = None
    if splash_path:
        try:
            anim_pre = GdkPixbuf.PixbufAnimation.new_from_file(str(splash_path))
        except Exception:
            anim_pre = None
    duration_sec = splash_duration_seconds(splash_path, anim_pre)
    deadline = time.monotonic() + duration_sec

    win = Gtk.ApplicationWindow(application=app)
    win.set_title("RainTux")
    win.set_resizable(False)
    win.set_modal(True)
    win.add_css_class("raintux-splash")

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    win.set_child(outer)

    img = Gtk.Image()
    img.set_hexpand(True)
    img.set_vexpand(True)
    outer.append(img)

    foot = Gtk.Label(label="RainTux")
    foot.set_halign(Gtk.Align.START)
    foot.set_margin_start(12)
    foot.set_margin_top(8)
    foot.set_margin_bottom(10)
    outer.append(foot)

    css = Gtk.CssProvider.new()
    load_css(css, 
        """
        window.raintux-splash { background: #000000; }
        window.raintux-splash label { color: white; font-weight: 700; font-size: 13pt;
          background: #1a237e; padding: 6px 14px; border-radius: 6px; }
        """
    )
    Gtk.StyleContext.add_provider_for_display(
        win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    anim: GdkPixbuf.PixbufAnimation | None = anim_pre
    it: GdkPixbuf.PixbufAnimationIter | None = None
    if splash_path and anim:
        try:
            it = anim.get_iter(None)
            pb0 = it.get_pixbuf()
            if pb0:
                win.set_default_size(pb0.get_width(), pb0.get_height() + 48)
                img.set_from_paintable(Gdk.Texture.new_for_pixbuf(pb0))
        except Exception:
            log.warning("Splash GIF failed: %s", splash_path, exc_info=True)
            anim = None
            it = None

    if not anim or not it:
        win.set_default_size(420, 240)
        img.set_from_icon_name("emblem-default-symbolic")

    def show_eula() -> None:
        Gtk.StyleContext.remove_provider_for_display(win.get_display(), css)
        win.destroy()
        _show_eula_dialog(app, on_done)

    def tick() -> bool:
        if time.monotonic() >= deadline:
            show_eula()
            return GLib.SOURCE_REMOVE
        if anim and it:
            try:
                it.advance(None)
                pb = it.get_pixbuf()
                if pb:
                    img.set_from_paintable(Gdk.Texture.new_for_pixbuf(pb))
                delay = it.get_delay_time()
                ms = max(20, delay) if delay > 0 else 50
            except Exception:
                ms = 50
        else:
            ms = 100
        GLib.timeout_add(ms, tick)
        return GLib.SOURCE_REMOVE

    win.present()
    GLib.timeout_add(30, tick)
