"""GTK4 window hosting a Cairo drawing surface for one skin overlay."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_LayerShell: Any = None

if sys.platform != "linux" and sys.platform != "darwin":
    pass
else:
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell

        _LayerShell = Gtk4LayerShell
    except (ImportError, ValueError, OSError):
        _LayerShell = None
        log.info("Gtk4LayerShell not available; using a normal GTK window (no desktop layer).")


_css_installed = False


def _install_css_once() -> None:
    """Add the transparent-window stylesheet to the display a single time."""
    global _css_installed
    if _css_installed:
        return
    css = Gtk.CssProvider.new()
    css.load_from_string("window.raintux-skin { background: transparent; }")
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        css,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _css_installed = True


class LayerShellWindow(Gtk.ApplicationWindow):
    """
    Transparent `` Gtk.ApplicationWindow`` with optional layer-shell placement.

    ``draw`` callback receives a Cairo image surface sized to the window.
    """

    def __init__(
        self,
        application: Gtk.Application,
        *,
        title: str,
        width: int,
        height: int,
        namespace: str = "raintux",
    ) -> None:
        super().__init__(application=application, title=title)
        self._namespace = namespace
        self._width = width
        self._height = height
        self._draw_cb: Callable[[cairo.Context], None] | None = None

        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(width, height)

        self.add_css_class("raintux-skin")
        _install_css_once()

        area = Gtk.DrawingArea()
        area.set_content_width(width)
        area.set_content_height(height)
        area.set_draw_func(self._on_draw, None)
        self.set_child(area)

        self._apply_layer_shell()
        # Click-through by default on Wayland layer shell when possible
        if _LayerShell:
            try:
                _LayerShell.set_keyboard_mode(self, _LayerShell.KeyboardMode.NONE)
                # Pointer passthrough is compositor-dependent; on many shells input
                # follows surface input region. TODO: refine via layer shell set_namespace.
            except Exception:
                log.debug("keyboard/pointer mode tweak skipped", exc_info=True)

    def set_draw_callback(self, cb: Callable[[cairo.Context], None]) -> None:
        """Replace the Cairo paint callback."""
        self._draw_cb = cb

    def invalidate(self) -> None:
        """Request a full redraw of the Cairo surface."""
        child = self.get_child()
        if isinstance(child, Gtk.DrawingArea):
            child.queue_draw()

    def resize_canvas(self, width: int, height: int) -> None:
        """Resize window and drawing area."""
        self._width = max(1, width)
        self._height = max(1, height)
        child = self.get_child()
        if isinstance(child, Gtk.DrawingArea):
            child.set_content_width(self._width)
            child.set_content_height(self._height)
        self.set_default_size(self._width, self._height)

    def _apply_layer_shell(self) -> None:
        if not _LayerShell:
            return
        try:
            _LayerShell.init_for_window(self)
            _LayerShell.set_layer(self, _LayerShell.Layer.BOTTOM)
            # set_anchor takes one edge at a time (edge, anchor_to_edge), not a bitmask.
            _LayerShell.set_anchor(self, _LayerShell.Edge.TOP, True)
            _LayerShell.set_anchor(self, _LayerShell.Edge.LEFT, True)
            _LayerShell.set_margin(self, _LayerShell.Edge.TOP, 0)
            _LayerShell.set_margin(self, _LayerShell.Edge.LEFT, 0)
            _LayerShell.set_namespace(self, self._namespace)
            try:
                if hasattr(_LayerShell, "set_exclusive_zone"):
                    _LayerShell.set_exclusive_zone(self, 0)
                elif hasattr(_LayerShell, "unset_exclusive_zone"):
                    _LayerShell.unset_exclusive_zone(self)
            except (AttributeError, TypeError):
                pass
        except Exception:
            log.exception("layer shell init failed; falling back to standard window")

    def _on_draw(self, area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int, _data: Any) -> None:
        if self._draw_cb:
            self._draw_cb(cr)
        else:
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
