"""GTK4 window hosting one skin: layer-shell placement, input, dragging, opacity, zoom."""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from raintux.core.position import layer_for_zpos
from raintux.gui.compat import load_css

if TYPE_CHECKING:
    from raintux.core.engine import RainTuxEngine
    from raintux.core.skin import Skin

log = logging.getLogger(__name__)

_LayerShell: Any = None
if sys.platform.startswith("linux"):
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell

        _LayerShell = Gtk4LayerShell
    except (ImportError, ValueError, OSError):
        _LayerShell = None

_BUTTONS = {1: "left", 2: "middle", 3: "right", 8: "x1", 9: "x2"}
_DRAG_THRESHOLD = 3.0

_css_installed = False


def layer_shell_available() -> bool:
    if _LayerShell is None:
        return False
    try:
        return bool(_LayerShell.is_supported())
    except Exception:
        return False


def _install_css_once() -> None:
    global _css_installed
    if _css_installed:
        return
    css = Gtk.CssProvider.new()
    load_css(css, "window.raintux-skin { background: transparent; box-shadow: none; }")
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed = True


def monitors() -> list[Any]:
    display = Gdk.Display.get_default()
    if display is None:
        return []
    mons = display.get_monitors()
    return [mons.get_item(i) for i in range(mons.get_n_items())]


def monitor_at(x: float, y: float) -> Any:
    """Monitor containing the point (falls back to the nearest one)."""
    best, best_d = None, math.inf
    for m in monitors():
        g = m.get_geometry()
        if g.x <= x < g.x + g.width and g.y <= y < g.y + g.height:
            return m
        cx = min(max(x, g.x), g.x + g.width)
        cy = min(max(y, g.y), g.y + g.height)
        d = (cx - x) ** 2 + (cy - y) ** 2
        if d < best_d:
            best, best_d = m, d
    return best


def _press_relative_pointer() -> bool:
    """Does the compositor keep pointer coordinates relative to the press-time surface position
    while a button is held? Sway does; compositors following the protocol literally don't."""
    return bool(os.environ.get("SWAYSOCK")) or "sway" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def _current_event(ctrl: Any) -> Gdk.Event | None:
    """PyGObject before 3.50 passes None for the GdkEvent of EventControllerLegacy::event
    (Ubuntu 24.04 ships 3.48); the controller still knows the event being handled."""
    try:
        return ctrl.get_current_event()
    except Exception:  # pragma: no cover
        return None


class SkinWindow(Gtk.ApplicationWindow):
    """Transparent undecorated window drawing a :class:`~raintux.core.skin.Skin`."""

    def __init__(self, engine: RainTuxEngine, skin: Skin) -> None:
        super().__init__(application=engine.app, title=skin.config)
        self.engine = engine
        self.skin = skin
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("raintux-skin")
        _install_css_once()
        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self._on_draw, None)
        self.area.set_focusable(True)
        self.set_child(self.area)
        self.set_default_size(1, 1)
        legacy = Gtk.EventControllerLegacy()
        legacy.connect("event", self._on_event)
        self.area.add_controller(legacy)
        self._setup_keyboard()
        self._setup_drop_target()
        self.layer = layer_shell_available()
        self._press: tuple[float, float, int, int] | None = None  # x, y, margin x, margin y
        self._dragging = False
        self._skin_buttons: set[str] = set()  # buttons whose press the skin consumed
        self._last_press = (0.0, 0.0, 0.0, 0)  # time, x, y, button
        self._fade_source = 0
        self._hidden_by_hover = False
        self._scroll_acc = [0.0, 0.0]
        self._input_rects: list[tuple[int, int, int, int]] | None = None
        self.zoom = 1.0
        self._logical = (1, 1)
        if self.layer:
            _LayerShell.init_for_window(self)
            _LayerShell.set_namespace(self, "raintux")
            _LayerShell.set_anchor(self, _LayerShell.Edge.TOP, True)
            _LayerShell.set_anchor(self, _LayerShell.Edge.LEFT, True)
            _LayerShell.set_exclusive_zone(self, -1)
            _LayerShell.set_keyboard_mode(self, _LayerShell.KeyboardMode.NONE)
        self.connect("realize", lambda *_: self.update_input_region(force=True))
        self.connect("notify::is-active", self._active_changed)
        self.apply_state()

    # ------------------------------------------------------------------ state
    def apply_state(self) -> None:
        st = self.skin.state
        if self.layer:
            _LayerShell.set_layer(self, getattr(_LayerShell.Layer, layer_for_zpos(st.z)))
        self.move_to(st.x, st.y)
        self.set_opacity(max(0.0, min(1.0, st.alpha / 255.0)))
        self.update_input_region(force=True)

    def move_to(self, x: int, y: int) -> None:
        """Place the window at virtual-desktop coordinates (Rainmeter WindowX/WindowY)."""
        self.skin.state.x, self.skin.state.y = int(x), int(y)
        self.skin.invalidate_builtins()
        if not self.layer:
            return
        w, h = self._logical
        mon = monitor_at(x + max(1, w * self.zoom) / 2, y + max(1, h * self.zoom) / 2)
        if mon is None:
            _LayerShell.set_margin(self, _LayerShell.Edge.LEFT, int(x))
            _LayerShell.set_margin(self, _LayerShell.Edge.TOP, int(y))
            return
        g = mon.get_geometry()
        _LayerShell.set_monitor(self, mon)
        _LayerShell.set_margin(self, _LayerShell.Edge.LEFT, int(x - g.x))
        _LayerShell.set_margin(self, _LayerShell.Edge.TOP, int(y - g.y))
        # Margins apply on the surface's next commit; draw now so a drag doesn't run ahead of it.
        self.area.queue_draw()

    def resize_canvas(self, width: int, height: int) -> None:
        self._logical = (max(1, width), max(1, height))
        pw, ph = max(1, round(width * self.zoom)), max(1, round(height * self.zoom))
        self.area.set_content_width(pw)
        self.area.set_content_height(ph)
        self.set_default_size(pw, ph)
        self.update_input_region()

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.1, min(8.0, zoom))
        self.skin.zoom = self.zoom
        self.resize_canvas(*self._logical)
        self.area.queue_draw()

    def update_input_region(self, force: bool = False) -> None:
        """Click-through everywhere (ClickThrough) or outside the skin's meters."""
        surface = self.get_surface()
        if surface is None:
            return
        z = self.zoom
        rects = [] if self.skin.state.click_through else [
            (int(x * z), int(y * z), int(math.ceil(w * z)), int(math.ceil(h * z))) for x, y, w, h in self.skin.input_rects()
        ]
        if not force and rects == self._input_rects:
            return
        self._input_rects = rects
        surface.set_input_region(cairo.Region([cairo.RectangleInt(x, y, w, h) for x, y, w, h in rects]))

    def set_skin(self, skin: Skin) -> None:
        self.skin = skin
        skin.zoom = self.zoom
        self.set_title(skin.config)

    # ------------------------------------------------------------------ keyboard
    def _setup_keyboard(self) -> None:
        self.im = Gtk.IMMulticontext()
        self.im.connect("commit", lambda _im, text: self._commit(text))
        keys = Gtk.EventControllerKey()
        keys.set_im_context(self.im)
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)

    def _commit(self, text: str) -> None:
        if self.skin.text_input(text):
            self.redraw()

    def _key_pressed(self, _ctrl: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if not self.skin.wants_keyboard():
            return False
        name = Gdk.keyval_name(keyval) or ""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if self.skin.key(name, ctrl, shift):
            return True
        if not ctrl:
            ch = Gdk.keyval_to_unicode(keyval)
            if ch and ch >= 32 and ch != 127:
                return self.skin.text_input(chr(ch))
        return False

    def set_keyboard(self, want: bool) -> None:
        """Take keyboard focus while a TextEdit is active (layer surfaces default to none)."""
        if self.layer:
            mode = _LayerShell.KeyboardMode.ON_DEMAND if want else _LayerShell.KeyboardMode.NONE
            _LayerShell.set_keyboard_mode(self, mode)
        if want:
            self.present()
            self.area.grab_focus()
            self.im.focus_in()
        else:
            self.im.focus_out()

    def _active_changed(self, *_a: Any) -> None:
        if not self.is_active() and self.skin.wants_keyboard():
            self.skin.keyboard_focus_lost()

    # ------------------------------------------------------------------ drag & drop of files
    def _setup_drop_target(self) -> None:
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        target.set_preload(False)
        target.connect("motion", lambda _t, x, y: Gdk.DragAction.COPY if self.skin.drop_motion(x / self.zoom, y / self.zoom) else 0)
        target.connect("leave", lambda _t: self.skin.drop_leave())
        target.connect("drop", self._dropped)
        self.area.add_controller(target)

    def _dropped(self, _t: Gtk.DropTarget, value: Any, x: float, y: float) -> bool:
        files = [Path(f.get_path()) for f in value.get_files() if f.get_path()]
        return self.skin.drop_files(files, x / self.zoom, y / self.zoom) if files else False

    # ------------------------------------------------------------------ drawing
    def _on_draw(self, _area: Gtk.DrawingArea, cr: cairo.Context, _w: int, _h: int, _data: Any) -> None:
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()
        try:
            if self.zoom != 1.0:
                cr.scale(self.zoom, self.zoom)
            self.skin.draw(cr)
        except Exception:
            log.exception("draw failed for %s", self.skin.config)

    def redraw(self) -> None:
        self.area.queue_draw()
        self.update_input_region()

    # ------------------------------------------------------------------ fading
    def fade_to(self, target: float, duration_ms: int, then: Any = None) -> None:
        if self._fade_source:
            GLib.source_remove(self._fade_source)
            self._fade_source = 0
        start = self.get_opacity()
        if duration_ms <= 0 or abs(start - target) < 1e-3:
            self.set_opacity(target)
            if then:
                then()
            return
        t0 = time.monotonic()

        def step() -> bool:
            k = min(1.0, (time.monotonic() - t0) * 1000 / duration_ms)
            self.set_opacity(start + (target - start) * k)
            if k >= 1.0:
                self._fade_source = 0
                if then:
                    then()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._fade_source = GLib.timeout_add(16, step)

    # ------------------------------------------------------------------ pointer
    def _margins(self) -> tuple[int, int]:
        return (self.skin.state.x, self.skin.state.y)

    def _pos(self, event: Gdk.Event) -> tuple[bool, float, float]:
        ok, x, y = event.get_position()
        return ok, x / self.zoom, y / self.zoom

    def _on_event(self, ctrl: Gtk.EventControllerLegacy, event: Gdk.Event | None) -> bool:
        event = event or _current_event(ctrl)
        if event is None:
            return False
        et = event.get_event_type()
        skin = self.skin
        if et in (Gdk.EventType.MOTION_NOTIFY, Gdk.EventType.ENTER_NOTIFY):
            ok, x, y = self._pos(event)
            if not ok:
                return False
            if self._press is not None and et == Gdk.EventType.MOTION_NOTIFY:
                self._drag_motion(x * self.zoom, y * self.zoom)
                return True
            if skin.state.hide_on_mouse_over and not self._hidden_by_hover:
                self._hidden_by_hover = True
                self.fade_to(0.05, skin.state.fade_duration)
            if self._skin_buttons and et == Gdk.EventType.MOTION_NOTIFY:
                if skin.mouse("drag", x, y):
                    return True
            skin.mouse("move", x, y)
            return False
        if et == Gdk.EventType.LEAVE_NOTIFY:
            if self._press is None and not self._skin_buttons:
                skin.mouse("leave")
                if self._hidden_by_hover:
                    self._hidden_by_hover = False
                    self.fade_to(skin.state.alpha / 255.0, skin.state.fade_duration)
            return False
        if et == Gdk.EventType.BUTTON_PRESS:
            ok, x, y = self._pos(event)
            name = _BUTTONS.get(event.get_button(), "")
            if not name:
                return False
            now = time.monotonic()
            t_prev, px, py, b_prev = self._last_press
            settings = Gtk.Settings.get_default()
            dbl_ms = settings.get_property("gtk-double-click-time") if settings else 400
            double = b_prev == event.get_button() and (now - t_prev) * 1000 <= dbl_ms and abs(px - x) < 5 and abs(py - y) < 5
            self._last_press = (0.0 if double else now, x, y, event.get_button())
            consumed = skin.mouse("double" if double else "down", x, y, name)
            if consumed:
                self._skin_buttons.add(name)
            elif name == "left" and skin.state.draggable:
                mx, my = self._margins()
                ok2, rx, ry = event.get_position()
                self._press = (rx, ry, mx, my)
                self._dragging = False
            return True
        if et == Gdk.EventType.BUTTON_RELEASE:
            ok, x, y = self._pos(event)
            name = _BUTTONS.get(event.get_button(), "")
            self._skin_buttons.discard(name)
            if name == "left" and self._press is not None:
                was_drag = self._dragging
                self._press = None
                self._dragging = False
                if was_drag:
                    self._drag_end()
                    return True
            ctrl = bool(event.get_modifier_state() & Gdk.ModifierType.CONTROL_MASK)
            if name == "right" and ctrl:
                self.engine.show_context_menu(self, x * self.zoom, y * self.zoom)
                return True
            consumed = skin.mouse("up", x, y, name)
            if name == "right" and not consumed:
                self.engine.show_context_menu(self, x * self.zoom, y * self.zoom)
            return True
        if et == Gdk.EventType.SCROLL:
            ok, x, y = self._pos(event)
            d = event.get_direction()
            kinds = {Gdk.ScrollDirection.UP: "scrollup", Gdk.ScrollDirection.DOWN: "scrolldown",
                     Gdk.ScrollDirection.LEFT: "scrollleft", Gdk.ScrollDirection.RIGHT: "scrollright"}
            if d == Gdk.ScrollDirection.SMOOTH:
                _ok2, dx, dy = event.get_deltas()
                self._scroll_acc[0] += dx
                self._scroll_acc[1] += dy
                fired = False
                while abs(self._scroll_acc[1]) >= 1.0:
                    up = self._scroll_acc[1] < 0
                    self._scroll_acc[1] += 1.0 if up else -1.0
                    skin.mouse("scrollup" if up else "scrolldown", x, y)
                    fired = True
                while abs(self._scroll_acc[0]) >= 1.0:
                    left = self._scroll_acc[0] < 0
                    self._scroll_acc[0] += 1.0 if left else -1.0
                    skin.mouse("scrollleft" if left else "scrollright", x, y)
                    fired = True
                return fired
            if d in kinds:
                skin.mouse(kinds[d], x, y)
                return True
        return False

    def _drag_motion(self, x: float, y: float) -> None:
        """Window drag; ``x``/``y`` are raw surface coordinates."""
        assert self._press is not None
        px, py, _mx, _my = self._press
        if not self._dragging:
            if abs(x - px) < _DRAG_THRESHOLD and abs(y - py) < _DRAG_THRESHOLD:
                return
            self._dragging = True
            if not self.layer:
                surface = self.get_surface()
                if isinstance(surface, Gdk.Toplevel):
                    seat = Gdk.Display.get_default().get_default_seat()
                    surface.begin_move(seat.get_pointer(), 1, px, py, Gdk.CURRENT_TIME)
                    self._press = None
                    self._dragging = False
                return
        if not self.layer:
            return
        cur_x, cur_y = self._margins()
        if _press_relative_pointer():
            # Sway keeps reporting coordinates relative to where the surface was when the
            # button went down (seatop_down), so measure from the press position.
            _px, _py, start_x, start_y = self._press
            nx, ny = int(round(start_x + x - px)), int(round(start_y + y - py))
        else:
            # Coordinates relative to the surface's current position: add this step.
            nx, ny = int(round(cur_x + x - px)), int(round(cur_y + y - py))
        if (nx, ny) != (cur_x, cur_y):
            self.move_to(nx, ny)

    def _drag_end(self) -> None:
        st = self.skin.state
        x, y = st.x, st.y
        if st.snap_edges:
            x, y = self.engine.snap_position(self, x, y)
        if st.keep_on_screen:
            x, y = self.engine.clamp_on_screen(self, x, y)
        self.move_to(x, y)
        self.engine.window_moved(self)


class CaptureOverlay(Gtk.Window):
    """Full-screen surface for ``!Mouse:StartCapture``: every pointer event goes to the measure."""

    def __init__(self, app: Gtk.Application, skin: Skin, measure: Any) -> None:
        super().__init__(application=app, decorated=False)
        self.skin = skin
        self.measure = measure
        area = Gtk.DrawingArea()
        color = measure.overlay_color

        def draw(_a: Any, cr: cairo.Context, _w: int, _h: int, _d: Any) -> None:
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(*color)
            cr.paint()

        area.set_draw_func(draw, None)
        cursor = {"arrow": "default", "hand": "pointer", "cross": "crosshair", "text": "text"}.get(
            str(measure.overlay_cursor).lower(), "default")
        area.set_cursor_from_name(cursor)
        self.set_child(area)
        self.add_css_class("raintux-skin")
        legacy = Gtk.EventControllerLegacy()
        legacy.connect("event", self._event)
        area.add_controller(legacy)
        self.origin = (0, 0)
        mon = monitor_at(skin.state.x + 1, skin.state.y + 1)
        if layer_shell_available():
            _LayerShell.init_for_window(self)
            _LayerShell.set_namespace(self, "raintux-capture")
            _LayerShell.set_layer(self, _LayerShell.Layer.OVERLAY)
            for edge in (_LayerShell.Edge.TOP, _LayerShell.Edge.BOTTOM, _LayerShell.Edge.LEFT, _LayerShell.Edge.RIGHT):
                _LayerShell.set_anchor(self, edge, True)
            _LayerShell.set_exclusive_zone(self, -1)
            if mon is not None:
                _LayerShell.set_monitor(self, mon)
        if mon is not None:
            g = mon.get_geometry()
            self.origin = (g.x, g.y)
        self.present()

    def _event(self, ctrl: Any, event: Gdk.Event | None) -> bool:
        event = event or _current_event(ctrl)
        if event is None:
            return False
        et = event.get_event_type()
        ok, x, y = event.get_position()
        if not ok:
            return False
        sx = x + self.origin[0] - self.skin.state.x
        sy = y + self.origin[1] - self.skin.state.y
        name = _BUTTONS.get(event.get_button(), "") if et in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE) else ""
        if et == Gdk.EventType.MOTION_NOTIFY:
            self.measure.moved(sx, sy)
        elif et == Gdk.EventType.BUTTON_PRESS and name:
            self.measure.pressed(name, sx, sy)
        elif et == Gdk.EventType.BUTTON_RELEASE and name:
            self.measure.released(name, sx, sy)
        return True


def open_uri(uri: str) -> None:
    Gio.AppInfo.launch_default_for_uri(uri, None)
