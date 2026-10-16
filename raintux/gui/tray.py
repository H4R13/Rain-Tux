"""Tray icon via StatusNotifierItem + DBusMenu (KDE, waybar, XFCE, Cinnamon, GNOME with AppIndicator).

Left click opens Manage (like Rainmeter's tray icon); the menu offers Manage, the skin list,
layouts, Refresh all, the skins folder and Exit.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gi.repository import Gio, GLib

if TYPE_CHECKING:
    from raintux.core.engine import RainTuxEngine

log = logging.getLogger(__name__)

SNI_XML = """
<node>
 <interface name="org.kde.StatusNotifierItem">
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
  <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
  <signal name="NewIcon"/>
  <signal name="NewToolTip"/>
  <signal name="NewStatus"><arg name="status" type="s"/></signal>
 </interface>
</node>
"""

MENU_XML = """
<node>
 <interface name="com.canonical.dbusmenu">
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
  <method name="GetLayout">
   <arg type="i" name="parentId" direction="in"/><arg type="i" name="recursionDepth" direction="in"/>
   <arg type="as" name="propertyNames" direction="in"/>
   <arg type="u" name="revision" direction="out"/><arg type="(ia{sv}av)" name="layout" direction="out"/>
  </method>
  <method name="GetGroupProperties">
   <arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/>
   <arg type="a(ia{sv})" name="properties" direction="out"/>
  </method>
  <method name="GetProperty">
   <arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/><arg type="v" name="value" direction="out"/>
  </method>
  <method name="Event">
   <arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/>
   <arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/>
  </method>
  <method name="EventGroup">
   <arg type="a(isvu)" name="events" direction="in"/><arg type="ai" name="idErrors" direction="out"/>
  </method>
  <method name="AboutToShow"><arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/></method>
  <method name="AboutToShowGroup">
   <arg type="ai" name="ids" direction="in"/><arg type="ai" name="updatesNeeded" direction="out"/>
   <arg type="ai" name="idErrors" direction="out"/>
  </method>
  <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
  <signal name="ItemsPropertiesUpdated">
   <arg type="a(ia{sv})" name="updatedProps"/><arg type="a(ias)" name="removedProps"/>
  </signal>
 </interface>
</node>
"""


def icon_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "icons" / "raintux.svg"


def _pixmaps() -> list[tuple[int, int, bytes]]:
    """Icon as ARGB32 (network byte order) pixmaps for hosts without our icon in the theme."""
    out = []
    try:
        import cairo
        import gi

        gi.require_version("Rsvg", "2.0")
        from gi.repository import Rsvg

        handle = Rsvg.Handle.new_from_file(str(icon_path()))
        for size in (22, 32, 48):
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
            cr = cairo.Context(surf)
            vp = Rsvg.Rectangle()
            vp.x, vp.y, vp.width, vp.height = 0, 0, size, size
            handle.render_document(cr, vp)
            surf.flush()
            data = bytes(surf.get_data())
            argb = bytearray(len(data))
            for i in range(0, len(data), 4):  # little-endian BGRA -> big-endian ARGB
                b, g, r, a = data[i], data[i + 1], data[i + 2], data[i + 3]
                argb[i:i + 4] = bytes((a, r, g, b))
            out.append((size, size, bytes(argb)))
    except Exception:
        log.debug("tray pixmap unavailable", exc_info=True)
    return out


class Tray:
    def __init__(self, engine: RainTuxEngine) -> None:
        self.engine = engine
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.revision = 1
        self.items: dict[int, tuple[str, Callable[[], Any] | None, dict[str, Any]]] = {}
        self.children: dict[int, list[int]] = {}
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._ids: list[int] = []
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._ids.append(self.bus.register_object("/StatusNotifierItem", sni, self._sni_call, self._sni_get, None))
        self._ids.append(self.bus.register_object("/MenuBar", menu, self._menu_call, self._menu_get, None))
        self._own = Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        self._pix = _pixmaps()
        self._register()
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                  "/org/freedesktop/DBus", "org.kde.StatusNotifierWatcher",
                                  Gio.DBusSignalFlags.NONE, lambda *a: self._register())
        self.rebuild()

    def _register(self) -> None:
        try:
            self.bus.call_sync("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                               "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.name,)), None,
                               Gio.DBusCallFlags.NONE, 1000, None)
        except GLib.Error as e:
            log.info("no system tray available (%s); use the RainTux app launcher to open Manage", e.message)

    # ------------------------------------------------------------------ SNI
    def _sni_get(self, _c: Any, _s: Any, _p: Any, _i: Any, prop: str) -> GLib.Variant | None:
        if prop == "Category":
            return GLib.Variant("s", "ApplicationStatus")
        if prop == "Id":
            return GLib.Variant("s", "raintux")
        if prop == "Title":
            return GLib.Variant("s", "RainTux")
        if prop == "Status":
            return GLib.Variant("s", "Active")
        if prop == "IconName":
            return GLib.Variant("s", "raintux")
        if prop == "IconThemePath":
            return GLib.Variant("s", str(icon_path().parent))
        if prop == "IconPixmap":
            return GLib.Variant("a(iiay)", self._pix)
        if prop == "ToolTip":
            n = len(self.engine.active)
            return GLib.Variant("(sa(iiay)ss)", ("raintux", [], "RainTux", f"{n} skin{'s' if n != 1 else ''} active"))
        if prop == "ItemIsMenu":
            return GLib.Variant("b", False)
        if prop == "Menu":
            return GLib.Variant("o", "/MenuBar")
        return None

    def _sni_call(self, _c: Any, _s: Any, _p: Any, _i: Any, method: str, params: GLib.Variant, inv: Any) -> None:
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(lambda: (self.engine.show_manage(), False)[1])
        inv.return_value(None)

    # ------------------------------------------------------------------ menu model
    def rebuild(self) -> None:
        eng = self.engine
        self.items = {0: ("", None, {"children-display": GLib.Variant("s", "submenu")})}
        self.children = {0: []}
        next_id = [1]

        def add(parent: int, label: str, cb: Callable[[], Any] | None = None, **props: GLib.Variant) -> int:
            i = next_id[0]
            next_id[0] += 1
            p = {"label": GLib.Variant("s", label)} if label else {}
            p.update(props)
            self.items[i] = (label, cb, p)
            self.children.setdefault(parent, []).append(i)
            return i

        def sep(parent: int) -> None:
            add(parent, "", None, type=GLib.Variant("s", "separator"))

        add(0, "Manage", eng.show_manage)
        sep(0)
        skins = add(0, "Skins", None, **{"children-display": GLib.Variant("s", "submenu")})
        for config, files in eng.list_configs().items():
            sub = add(skins, config.replace("_", "__"), None, **{"children-display": GLib.Variant("s", "submenu")})
            for f in files:
                act = eng.active.get(config.lower())
                on = act is not None and act.skin.ini_path.name == f

                def toggle(config: str = config, f: str = f, on: bool = on) -> None:
                    eng.deactivate_config(config) if on else eng._safe_activate(config, f)

                add(sub, f.replace("_", "__"), toggle, **{"toggle-type": GLib.Variant("s", "checkmark"),
                                                          "toggle-state": GLib.Variant("i", 1 if on else 0)})
        layouts = add(0, "Layouts", None, **{"children-display": GLib.Variant("s", "submenu")})
        for name in eng.list_layouts():
            add(layouts, name.replace("_", "__"), lambda name=name: eng.load_layout(name))
        sep(0)
        add(0, "Refresh all", eng.refresh_all)
        add(0, "Open skins folder", lambda: Gio.AppInfo.launch_default_for_uri(eng.primary_root().as_uri(), None))
        sep(0)
        add(0, "Exit", eng.app.quit)
        self.revision += 1
        try:
            self.bus.emit_signal(None, "/MenuBar", "com.canonical.dbusmenu", "LayoutUpdated",
                                 GLib.Variant("(ui)", (self.revision, 0)))
        except GLib.Error:
            pass

    def _layout(self, item_id: int, depth: int) -> tuple[int, dict[str, GLib.Variant], list[GLib.Variant]]:
        _label, _cb, props = self.items.get(item_id, ("", None, {}))
        kids = []
        if depth != 0:
            for c in self.children.get(item_id, []):
                kids.append(GLib.Variant("(ia{sv}av)", self._layout(c, depth - 1)))
        return (item_id, props, kids)

    def _menu_get(self, _c: Any, _s: Any, _p: Any, _i: Any, prop: str) -> GLib.Variant | None:
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(prop)

    def _menu_call(self, _c: Any, _s: Any, _p: Any, _i: Any, method: str, params: GLib.Variant, inv: Any) -> None:
        args = params.unpack()
        if method == "GetLayout":
            parent, depth, _props = args
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, self._layout(parent, depth))))
            return
        if method == "GetGroupProperties":
            ids, _names = args
            inv.return_value(GLib.Variant("(a(ia{sv}))", ([(i, self.items[i][2]) for i in ids if i in self.items],)))
            return
        if method == "GetProperty":
            i, name = args
            v = self.items.get(i, ("", None, {}))[2].get(name, GLib.Variant("s", ""))
            inv.return_value(GLib.Variant("(v)", (v,)))
            return
        if method in ("Event", "EventGroup"):
            events = [args[:4]] if method == "Event" else args[0]
            for ev in events:
                item_id, kind = ev[0], ev[1]
                if kind == "clicked" and item_id in self.items and self.items[item_id][1] is not None:
                    cb = self.items[item_id][1]
                    GLib.idle_add(lambda cb=cb: (cb(), self.rebuild(), False)[2])
            inv.return_value(None if method == "Event" else GLib.Variant("(ai)", ([],)))
            return
        if method == "AboutToShow":
            self.rebuild()
            inv.return_value(GLib.Variant("(b)", (True,)))
            return
        if method == "AboutToShowGroup":
            self.rebuild()
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
            return
        inv.return_value(None)

    def close(self) -> None:
        for i in self._ids:
            self.bus.unregister_object(i)
        Gio.bus_unown_name(self._own)
