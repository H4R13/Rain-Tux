"""GTK4 window hosting one skin: layer-shell placement, input, dragging, opacity."""

from __future__ import annotations

import logging
import math
import sys
import time
from typing import TYPE_CHECKING, Any

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

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
_SNAP = 10

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
    css.load_from_string("window.raintux-skin { background: transparent; box-shadow: none; }")
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
        self.set_child(self.area)
        self.set_default_size(1, 1)
        legacy = Gtk.EventControllerLegacy()
        legacy.connect("event", self._on_event)
        self.area.add_controller(legacy)
        self.layer = layer_shell_available()
        self._press: tuple[float, float, int, int] | None = None  # x, y, margin x, margin y
        self._dragging = False
        self._last_press = (0.0, 0.0, 0.0, 0)  # time, x, y, button
        self._fade_source = 0
        self._hidden_by_hover = False
        self._scroll_acc = [0.0, 0.0]
        self._input_rects: list[tuple[int, int, int, int]] | None = None
        if self.layer:
            _LayerShell.init_for_window(self)
            _LayerShell.set_namespace(self, "raintux")
            _LayerShell.set_anchor(self, _LayerShell.Edge.TOP, True)
            _LayerShell.set_anchor(self, _LayerShell.Edge.LEFT, True)
            _LayerShell.set_exclusive_zone(self, -1)
            _LayerShell.set_keyboard_mode(self, _LayerShell.KeyboardMode.NONE)
        self.connect("realize", lambda *_: self.update_input_region(force=True))
        self.apply_state()

    # ------------------------------------------------------------------ state
    def apply_state(self) -> None:
        st = self.skin.state
        if self.layer:
            layer = {-2: _LayerShell.Layer.BACKGROUND, -1: _LayerShell.Layer.BOTTOM, 0: _LayerShell.Layer.BOTTOM,
                     1: _LayerShell.Layer.TOP, 2: _LayerShell.Layer.OVERLAY}.get(st.z, _LayerShell.Layer.BOTTOM)
            _LayerShell.set_layer(self, layer)
        self.move_to(st.x, st.y)
        self.set_opacity(max(0.0, min(1.0, st.alpha / 255.0)))
        self.update_input_region(force=True)

    def move_to(self, x: int, y: int) -> None:
        """Place the window at virtual-desktop coordinates (Rainmeter WindowX/WindowY)."""
        self.skin.state.x, self.skin.state.y = int(x), int(y)
        self.skin.invalidate_builtins()
        if not self.layer:
            return
        mon = monitor_at(x + max(1, self.skin.width) / 2, y + max(1, self.skin.height) / 2)
        if mon is None:
            _LayerShell.set_margin(self, _LayerShell.Edge.LEFT, int(x))
            _LayerShell.set_margin(self, _LayerShell.Edge.TOP, int(y))
            return
        g = mon.get_geometry()
        _LayerShell.set_monitor(self, mon)
        _LayerShell.set_margin(self, _LayerShell.Edge.LEFT, int(x - g.x))
        _LayerShell.set_margin(self, _LayerShell.Edge.TOP, int(y - g.y))

    def resize_canvas(self, width: int, height: int) -> None:
        self.area.set_content_width(max(1, width))
        self.area.set_content_height(max(1, height))
        self.set_default_size(max(1, width), max(1, height))
        self.update_input_region()

    def update_input_region(self, force: bool = False) -> None:
        """Click-through everywhere (ClickThrough) or outside the skin's meters."""
        surface = self.get_surface()
        if surface is None:
            return
        rects = [] if self.skin.state.click_through else self.skin.input_rects()
        if not force and rects == self._input_rects:
            return
        self._input_rects = rects
        region = cairo.Region([cairo.RectangleInt(x, y, w, h) for x, y, w, h in rects])
        surface.set_input_region(region)

    def set_skin(self, skin: Skin) -> None:
        self.skin = skin
        self.set_title(skin.config)

    # ------------------------------------------------------------------ drawing
    def _on_draw(self, _area: Gtk.DrawingArea, cr: cairo.Context, _w: int, _h: int, _data: Any) -> None:
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()
        try:
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

    # ------------------------------------------------------------------ input
    def _margins(self) -> tuple[int, int]:
        return (self.skin.state.x, self.skin.state.y)

    def _on_event(self, _ctrl: Gtk.EventControllerLegacy, event: Gdk.Event) -> bool:
        et = event.get_event_type()
        skin = self.skin
        if et in (Gdk.EventType.MOTION_NOTIFY, Gdk.EventType.ENTER_NOTIFY):
            ok, x, y = event.get_position()
            if not ok:
                return False
            if self._press is not None and et == Gdk.EventType.MOTION_NOTIFY:
                self._drag_motion(x, y)
                return True
            if skin.state.hide_on_mouse_over and not self._hidden_by_hover:
                self._hidden_by_hover = True
                self.fade_to(0.05, skin.state.fade_duration)
            skin.mouse("move", x, y)
            return False
        if et == Gdk.EventType.LEAVE_NOTIFY:
            if self._press is None:
                skin.mouse("leave")
                if self._hidden_by_hover:
                    self._hidden_by_hover = False
                    self.fade_to(skin.state.alpha / 255.0, skin.state.fade_duration)
            return False
        if et == Gdk.EventType.BUTTON_PRESS:
            ok, x, y = event.get_position()
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
            if name == "left" and not consumed and skin.state.draggable:
                mx, my = self._margins()
                self._press = (x, y, mx, my)
                self._dragging = False
            return True
        if et == Gdk.EventType.BUTTON_RELEASE:
            ok, x, y = event.get_position()
            name = _BUTTONS.get(event.get_button(), "")
            if name == "left" and self._press is not None:
                was_drag = self._dragging
                self._press = None
                self._dragging = False
                if was_drag:
                    self._drag_end()
                    return True
            ctrl = bool(event.get_modifier_state() & Gdk.ModifierType.CONTROL_MASK)
            if name == "right" and ctrl:
                self.engine.show_context_menu(self, x, y)
                return True
            consumed = skin.mouse("up", x, y, name)
            if name == "right" and not consumed:
                self.engine.show_context_menu(self, x, y)
            return True
        if et == Gdk.EventType.SCROLL:
            ok, x, y = event.get_position()
            d = event.get_direction()
            kinds = {Gdk.ScrollDirection.UP: "scrollup", Gdk.ScrollDirection.DOWN: "scrolldown",
                     Gdk.ScrollDirection.LEFT: "scrollleft", Gdk.ScrollDirection.RIGHT: "scrollright"}
            if d == Gdk.ScrollDirection.SMOOTH:
                ok2, dx, dy = event.get_deltas()
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
        # Event coordinates are relative to where the surface was when the event was
        # generated; moving by (x - px) keeps the grab point under the pointer.
        cur_x, cur_y = self._margins()
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
