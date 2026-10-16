"""Right-click menu on a skin, laid out like Rainmeter's."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from gi.repository import Gdk, Gio, GLib, Gtk

if TYPE_CHECKING:
    from raintux.core.engine import RainTuxEngine
    from raintux.wayland.layer_window import SkinWindow

log = logging.getLogger(__name__)

POSITIONS = [(2, "Stay topmost"), (1, "Topmost"), (0, "Normal"), (-1, "Bottom"), (-2, "On desktop")]
TOGGLES = [
    ("hide_on_mouse_over", "Hide on mouse over"),
    ("draggable", "Draggable"),
    ("save_position", "Save position"),
    ("snap_edges", "Snap to edges"),
    ("click_through", "Click through"),
    ("keep_on_screen", "Keep on screen"),
]


def _item(label: str, action: str, target: Any = None) -> Gio.MenuItem:
    it = Gio.MenuItem.new(label, None)
    if target is None:
        it.set_action_and_target_value(action, None)
    else:
        it.set_action_and_target_value(action, target)
    return it


def build_menu(engine: RainTuxEngine, window: SkinWindow) -> tuple[Gio.Menu, Gio.SimpleActionGroup]:
    skin = window.skin
    st = skin.state
    group = Gio.SimpleActionGroup()
    menu = Gio.Menu()

    def action(name: str, cb: Any, ptype: str | None = None, state: GLib.Variant | None = None) -> None:
        vt = GLib.VariantType.new(ptype) if ptype else None
        a = Gio.SimpleAction.new_stateful(name, vt, state) if state is not None else Gio.SimpleAction.new(name, vt)
        a.connect("activate", cb)
        group.add_action(a)

    title = Gio.Menu()
    title.append_item(_item(skin.config, "skin.title"))
    menu.append_section(None, title)
    disabled = Gio.SimpleAction.new("title", None)
    disabled.set_enabled(False)
    group.add_action(disabled)

    # Variants (other .ini files of this config)
    variants = Gio.Menu()
    for ini in sorted(skin.root.glob("*.ini")):
        variants.append_item(_item(ini.name, "skin.variant", GLib.Variant("s", ini.name)))
    action("variant", lambda a, v: engine.activate_config(skin.config, v.get_string()), "s",
           GLib.Variant("s", skin.ini_path.name))

    settings = Gio.Menu()
    pos = Gio.Menu()
    for z, label in POSITIONS:
        pos.append_item(_item(label, "skin.zpos", GLib.Variant("i", z)))
    settings.append_submenu("Position", pos)

    def set_z(a: Gio.SimpleAction, v: GLib.Variant) -> None:
        st.z = v.get_int32()
        window.apply_state()
        engine.schedule_save()

    action("zpos", set_z, "i", GLib.Variant("i", st.z))

    trans = Gio.Menu()
    for pct in range(0, 100, 10):
        trans.append_item(_item(f"{pct}%", "skin.alpha", GLib.Variant("i", pct)))
    current_pct = int(round((255 - st.alpha) / 255 * 10)) * 10

    def set_alpha(a: Gio.SimpleAction, v: GLib.Variant) -> None:
        st.alpha = int(round(255 * (100 - v.get_int32()) / 100))
        window.apply_state()
        engine.schedule_save()

    action("alpha", set_alpha, "i", GLib.Variant("i", current_pct))
    settings.append_submenu("Transparency", trans)

    toggles = Gio.Menu()
    for attr, label in TOGGLES:
        toggles.append_item(_item(label, f"skin.{attr}"))

        def flip(a: Gio.SimpleAction, _v: Any, attr: str = attr) -> None:
            setattr(st, attr, not getattr(st, attr))
            window.apply_state()
            engine.schedule_save()

        action(attr, flip, None, GLib.Variant("b", bool(getattr(st, attr))))
    settings.append_section(None, toggles)

    body = Gio.Menu()
    if len(list(skin.root.glob("*.ini"))) > 1:
        body.append_submenu("Variants", variants)
    body.append_submenu("Settings", settings)
    menu.append_section(None, body)

    custom = skin.context_items()
    if custom:
        cm = Gio.Menu()
        for i, (label, act) in enumerate(custom):
            if label.strip("-") == "" and label:
                continue
            cm.append_item(_item(label, "skin.custom", GLib.Variant("i", i)))
        action("custom", lambda a, v: skin.execute(custom[v.get_int32()][1], skin.rm), "i")
        menu.append_section(None, cm)

    ops = Gio.Menu()
    ops.append_item(_item("Manage skin", "skin.manage"))
    ops.append_item(_item("Edit skin", "skin.edit"))
    ops.append_item(_item("Refresh skin", "skin.refresh"))
    ops.append_item(_item("Unload skin", "skin.unload"))
    action("manage", lambda *_: engine.show_manage(skin.config))
    action("edit", lambda *_: engine.open_in_editor(skin.ini_path))
    action("refresh", lambda *_: GLib.idle_add(lambda: (engine.refresh_config(skin.config), False)[1]))
    action("unload", lambda *_: GLib.idle_add(lambda: (engine.deactivate_config(skin.config), False)[1]))
    menu.append_section(None, ops)

    app_menu = Gio.Menu()
    skins_menu = Gio.Menu()
    for config, files in engine.list_configs().items():
        sub = Gio.Menu()
        for f in files:
            sub.append_item(_item(f, "skin.toggleskin", GLib.Variant("s", config + "|" + f)))
        skins_menu.append_submenu(config, sub)

    def toggle_skin(a: Gio.SimpleAction, v: GLib.Variant) -> None:
        config, _sep, f = v.get_string().partition("|")
        act = engine.active.get(config.lower())
        if act is not None and act.skin.ini_path.name == f:
            engine.deactivate_config(config)
        else:
            engine.activate_config(config, f)

    action("toggleskin", toggle_skin, "s")
    app_menu.append_submenu("Skins", skins_menu)
    app_menu.append_item(_item("Manage", "skin.appmanage"))
    app_menu.append_item(_item("Refresh all", "skin.refreshall"))
    app_menu.append_item(_item("Open skins folder", "skin.openfolder"))
    app_menu.append_item(_item("Exit", "skin.quit"))
    action("appmanage", lambda *_: engine.show_manage())
    action("refreshall", lambda *_: GLib.idle_add(lambda: (engine.refresh_all(), False)[1]))
    action("openfolder", lambda *_: Gio.AppInfo.launch_default_for_uri(engine.primary_root().as_uri(), None))
    action("quit", lambda *_: engine.app.quit())
    tail = Gio.Menu()
    tail.append_submenu("RainTux", app_menu)
    menu.append_section(None, tail)
    return menu, group


def show_context_menu(engine: RainTuxEngine, window: SkinWindow, x: float, y: float) -> None:
    menu, group = build_menu(engine, window)
    window.insert_action_group("skin", group)
    pop = Gtk.PopoverMenu.new_from_model(menu)
    pop.set_parent(window.area)
    pop.set_has_arrow(False)
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    pop.set_pointing_to(rect)
    pop.set_position(Gtk.PositionType.BOTTOM)
    layer = getattr(window, "layer", False)
    if layer:
        from raintux.wayland.layer_window import _LayerShell

        _LayerShell.set_keyboard_mode(window, _LayerShell.KeyboardMode.ON_DEMAND)

    def closed(p: Gtk.PopoverMenu) -> None:
        if layer:
            from raintux.wayland.layer_window import _LayerShell

            _LayerShell.set_keyboard_mode(window, _LayerShell.KeyboardMode.NONE)
        GLib.idle_add(lambda: (p.unparent(), False)[1])

    pop.connect("closed", closed)
    pop.popup()


def show_custom_menu(engine: RainTuxEngine, window: SkinWindow, x: float, y: float) -> None:
    """``!SkinCustomMenu``: only the skin's ContextTitle / ContextAction items."""
    skin = window.skin
    items = skin.context_items()
    if not items:
        return
    group = Gio.SimpleActionGroup()
    menu = Gio.Menu()
    for i, (label, _act) in enumerate(items):
        if label.strip("-"):
            menu.append_item(_item(label, "custom.run", GLib.Variant("i", i)))
    a = Gio.SimpleAction.new("run", GLib.VariantType.new("i"))
    a.connect("activate", lambda _a, v: skin.execute(items[v.get_int32()][1], skin.rm))
    group.add_action(a)
    window.insert_action_group("custom", group)
    pop = Gtk.PopoverMenu.new_from_model(menu)
    pop.set_parent(window.area)
    pop.set_has_arrow(False)
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    pop.set_pointing_to(rect)
    pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
    pop.popup()
