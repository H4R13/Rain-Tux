"""One-line text input shown over a skin (InputText plugin)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from gi.repository import Gdk, GLib, Gtk

from raintux.gui.compat import load_css

from raintux.measures.plugins.inputtext import entry_css
from raintux.wayland.layer_window import _LayerShell, layer_shell_available, monitor_at

if TYPE_CHECKING:
    from raintux.core.skin import Skin

log = logging.getLogger(__name__)


def ask_text(app: Gtk.Application, skin: Skin, rect: tuple[float, float, float, float], style: dict[str, Any],
             default: str, password: bool, callback: Callable[[str | None], None]) -> Gtk.Window:
    x, y, w, h = rect
    win = Gtk.Window(application=app, decorated=False, resizable=False)
    win.set_default_size(int(w), int(h))
    entry = Gtk.Entry()
    entry.add_css_class("raintux-input")
    entry.set_text(default)
    entry.set_visibility(not password)
    entry.set_size_request(int(w), int(h))
    if style.get("limit"):
        entry.set_max_length(int(style["limit"]))
    if style.get("number"):
        entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
    align = str(style.get("align", "left"))
    entry.set_alignment(0.5 if align.startswith("center") else 1.0 if align.startswith("right") else 0.0)
    css = Gtk.CssProvider()
    load_css(css, entry_css(style) + " window.raintux-input-win { background: transparent; }")
    entry.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    win.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    win.add_css_class("raintux-input-win")
    win.set_child(entry)
    done = {"fired": False}

    def finish(text: str | None) -> None:
        if done["fired"]:
            return
        done["fired"] = True
        win.destroy()
        GLib.idle_add(lambda: (callback(text), False)[1])

    entry.connect("activate", lambda e: finish(e.get_text()))
    keys = Gtk.EventControllerKey()
    keys.connect("key-pressed", lambda _c, kv, _code, _st: (finish(None), True)[1] if kv == Gdk.KEY_Escape else False)
    entry.add_controller(keys)
    if style.get("focus_dismiss", True):
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: GLib.timeout_add(100, lambda: (finish(None), False)[1]))
        entry.add_controller(focus)
    gx, gy = skin.state.x + int(x), skin.state.y + int(y)
    if layer_shell_available():
        _LayerShell.init_for_window(win)
        _LayerShell.set_namespace(win, "raintux-input")
        _LayerShell.set_layer(win, _LayerShell.Layer.OVERLAY)
        _LayerShell.set_keyboard_mode(win, _LayerShell.KeyboardMode.EXCLUSIVE)
        _LayerShell.set_anchor(win, _LayerShell.Edge.TOP, True)
        _LayerShell.set_anchor(win, _LayerShell.Edge.LEFT, True)
        _LayerShell.set_exclusive_zone(win, -1)
        mon = monitor_at(gx, gy)
        if mon is not None:
            g = mon.get_geometry()
            _LayerShell.set_monitor(win, mon)
            gx, gy = gx - g.x, gy - g.y
        _LayerShell.set_margin(win, _LayerShell.Edge.LEFT, gx)
        _LayerShell.set_margin(win, _LayerShell.Edge.TOP, gy)
    win.present()
    entry.grab_focus()
    entry.select_region(0, -1)
    return win
