"""The Manage window (Rainmeter's "Manage"): Skins, Layouts, Settings and Log tabs."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gi.repository import Gio, GLib, Gtk, Pango

from raintux.gui.compat import load_css

from raintux.gui.context_menu import POSITIONS, TOGGLES

if TYPE_CHECKING:
    from raintux.core.engine import RainTuxEngine

log = logging.getLogger(__name__)

GET_SKINS_URL = "https://www.deviantart.com/rainmeter"

_CSS = """
.rt-sidebar row { padding: 6px 10px; }
.rt-sidebar row.rt-group { font-weight: 700; padding-top: 12px; opacity: 0.8; }
.rt-active { color: #2a7de1; font-weight: 700; }
.rt-title { font-size: 18pt; font-weight: 700; }
.rt-dim { opacity: 0.7; }
.rt-log { font-family: monospace; }
"""


def read_metadata(ini: Path) -> dict[str, str]:
    from raintux.core.skin_parser import parse_skin_ini

    try:
        parsed = parse_skin_ini(ini)
    except Exception:
        return {}
    for name, sec in parsed.sections.items():
        if name.lower() == "metadata":
            return {k.lower(): v for k, v in sec.items()}
    return {}


def _margins(w: Gtk.Widget, m: int = 18) -> Gtk.Widget:
    for side in ("top", "bottom", "start", "end"):
        getattr(w, f"set_margin_{side}")(m)
    return w


class ManageWindow(Gtk.ApplicationWindow):
    def __init__(self, engine: RainTuxEngine) -> None:
        super().__init__(application=engine.app, title="RainTux — Manage")
        self.engine = engine
        self.set_default_size(940, 620)
        css = Gtk.CssProvider()
        load_css(css, _CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.StackSwitcher(stack=self.stack))
        self.set_titlebar(header)
        refresh_all = Gtk.Button(label="Refresh all")
        refresh_all.connect("clicked", lambda *_: (engine.refresh_all(), self.refresh()))
        header.pack_start(refresh_all)
        install = Gtk.Button(label="Install skin…")
        install.connect("clicked", lambda *_: self._choose_package())
        header.pack_end(install)
        folder = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open skins folder")
        folder.connect("clicked", lambda *_: Gio.AppInfo.launch_default_for_uri(engine.primary_root().as_uri(), None))
        header.pack_end(folder)
        get = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Get more skins")
        get.connect("clicked", lambda *_: Gio.AppInfo.launch_default_for_uri(GET_SKINS_URL, None))
        header.pack_end(get)
        self.set_child(self.stack)

        self.stack.add_titled(self._skins_page(), "skins", "Skins")
        self.stack.add_titled(self._layouts_page(), "layouts", "Layouts")
        self.stack.add_titled(self._settings_page(), "settings", "Settings")
        self.stack.add_titled(self._log_page(), "log", "Log")
        self.connect("destroy", lambda *_: self._detach_log())

    # ================================================================== Skins
    def _skins_page(self) -> Gtk.Widget:
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=300)
        self.list = Gtk.ListBox()
        self.list.add_css_class("rt-sidebar")
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-selected", self._on_select)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.list)
        paned.set_start_child(scroller)
        self.detail = _margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12))
        dscroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        dscroll.set_child(self.detail)
        paned.set_end_child(dscroll)
        self.selected: str | None = None
        return paned

    def refresh(self, select: str | None = None) -> None:
        if select:
            self.selected = select
            self.stack.set_visible_child_name("skins")
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        last_group = None
        select_row = None
        for config in self.engine.list_configs():
            group = config.split("\\")[0]
            if group != last_group:
                hdr = Gtk.ListBoxRow(selectable=False, activatable=False)
                hdr.add_css_class("rt-group")
                hdr.set_child(Gtk.Label(label=group, xalign=0))
                self.list.append(hdr)
                last_group = group
            depth = config.count("\\")
            label = Gtk.Label(label=config.split("\\")[-1] if depth else config, xalign=0)
            label.set_margin_start(12 * depth)
            if config.lower() in self.engine.active:
                label.add_css_class("rt-active")
            row = Gtk.ListBoxRow()
            row.set_child(label)
            row.config = config  # type: ignore[attr-defined]
            self.list.append(row)
            if self.selected and config.lower() == self.selected.lower():
                select_row = row
        if select_row is not None:
            self.list.select_row(select_row)
        else:
            self._show(None)
        if hasattr(self, "layout_list"):
            self._fill_layouts()

    def _on_select(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        cfg = getattr(row, "config", None) if row is not None else None
        if cfg is not None:
            self.selected = cfg
        self._show(cfg)

    def _show(self, config: str | None) -> None:
        while (child := self.detail.get_first_child()) is not None:
            self.detail.remove(child)
        eng = self.engine
        if config is None:
            n = len(eng.active)
            lbl = Gtk.Label(label=f"{n} skin{'s' if n != 1 else ''} active.\nSelect a skin on the left.", xalign=0)
            lbl.add_css_class("rt-dim")
            self.detail.append(lbl)
            return
        found = eng.find_config_dir(config)
        files = eng.list_configs().get(config, [])
        act = eng.active.get(config.lower())
        title = Gtk.Label(label=config, xalign=0)
        title.add_css_class("rt-title")
        self.detail.append(title)

        row = Gtk.Box(spacing=8)
        variant = Gtk.DropDown.new_from_strings(files or ["(no .ini files)"])
        if act is not None and act.skin.ini_path.name in files:
            variant.set_selected(files.index(act.skin.ini_path.name))
        row.append(variant)

        def chosen() -> str | None:
            i = variant.get_selected()
            return files[i] if 0 <= i < len(files) else None

        load = Gtk.Button(label="Load" if act is None else "Load variant")
        load.add_css_class("suggested-action")
        load.connect("clicked", lambda *_: self._do(lambda: eng.activate_config(config, chosen())))
        row.append(load)
        if act is not None:
            for label, fn in (("Refresh", lambda: eng.refresh_config(config)), ("Unload", lambda: eng.deactivate_config(config))):
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda *_, fn=fn: self._do(fn))
                row.append(b)
        edit = Gtk.Button(label="Edit")
        edit.connect("clicked", lambda *_: chosen() and found and eng.open_in_editor(found[1] / chosen()))
        row.append(edit)
        self.detail.append(row)

        ini = found[1] / files[variant.get_selected()] if found and files else None
        meta = read_metadata(ini) if ini else {}
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        r = 0
        for key in ("name", "author", "version", "license"):
            if meta.get(key):
                grid.attach(Gtk.Label(label=key.title(), xalign=0, css_classes=["rt-dim"]), 0, r, 1, 1)
                grid.attach(Gtk.Label(label=meta[key], xalign=0, selectable=True, wrap=True), 1, r, 1, 1)
                r += 1
        if r:
            self.detail.append(grid)
        if meta.get("information"):
            info = Gtk.Label(label=meta["information"].replace("|", "\n"), xalign=0, wrap=True, selectable=True)
            info.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.detail.append(info)
        if act is None:
            return

        st = act.skin.state
        self.detail.append(Gtk.Separator())
        sg = Gtk.Grid(column_spacing=12, row_spacing=8)
        xs = Gtk.SpinButton.new_with_range(-100000, 100000, 1)
        ys = Gtk.SpinButton.new_with_range(-100000, 100000, 1)
        xs.set_value(st.x)
        ys.set_value(st.y)

        def moved(*_a: Any) -> None:
            act.window.move_to(int(xs.get_value()), int(ys.get_value()))
            eng.window_moved(act.window)

        xs.connect("value-changed", moved)
        ys.connect("value-changed", moved)
        coords = Gtk.Box(spacing=6)
        coords.append(xs)
        coords.append(ys)
        sg.attach(Gtk.Label(label="Coordinates", xalign=0), 0, 0, 1, 1)
        sg.attach(coords, 1, 0, 1, 1)

        zs = [z for z, _l in POSITIONS]
        pos = Gtk.DropDown.new_from_strings([label for _z, label in POSITIONS])
        pos.set_selected(zs.index(st.z) if st.z in zs else 2)

        def set_pos(*_a: Any) -> None:
            st.z = POSITIONS[pos.get_selected()][0]
            act.window.apply_state()
            eng.schedule_save()

        pos.connect("notify::selected", set_pos)
        sg.attach(Gtk.Label(label="Position", xalign=0), 0, 1, 1, 1)
        sg.attach(pos, 1, 1, 1, 1)

        trans = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 90, 10)
        trans.set_value(round((255 - st.alpha) / 255 * 100))
        trans.set_hexpand(True)

        def set_trans(*_a: Any) -> None:
            st.alpha = int(round(255 * (100 - trans.get_value()) / 100))
            act.window.apply_state()
            eng.schedule_save()

        trans.connect("value-changed", set_trans)
        sg.attach(Gtk.Label(label="Transparency", xalign=0), 0, 2, 1, 1)
        sg.attach(trans, 1, 2, 1, 1)
        order = Gtk.SpinButton.new_with_range(-999, 999, 1)
        cs = eng.state.config(config)
        order.set_value(cs.load_order)
        order.connect("value-changed", lambda *_: (setattr(cs, "load_order", int(order.get_value())), eng.schedule_save()))
        sg.attach(Gtk.Label(label="Load order", xalign=0), 0, 3, 1, 1)
        sg.attach(order, 1, 3, 1, 1)
        zoom = Gtk.SpinButton.new_with_range(25, 400, 5)
        zoom.set_value(round(act.window.zoom * 100))
        zoom.connect("value-changed", lambda *_: act.window.set_zoom(zoom.get_value() / 100))
        sg.attach(Gtk.Label(label="Zoom %", xalign=0), 0, 4, 1, 1)
        sg.attach(zoom, 1, 4, 1, 1)
        self.detail.append(sg)

        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3)
        for attr, label in TOGGLES:
            cb = Gtk.CheckButton(label=label, active=bool(getattr(st, attr)))

            def toggled(btn: Gtk.CheckButton, attr: str = attr) -> None:
                setattr(st, attr, btn.get_active())
                act.window.apply_state()
                eng.schedule_save()

            cb.connect("toggled", toggled)
            flow.append(cb)
        self.detail.append(flow)

    # ================================================================== Layouts
    def _layouts_page(self) -> Gtk.Widget:
        box = _margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12))
        intro = Gtk.Label(label="A layout remembers which skins are loaded and where. Loading one replaces the "
                                "current skins.", xalign=0, wrap=True)
        intro.add_css_class("rt-dim")
        box.append(intro)
        save_row = Gtk.Box(spacing=8)
        self.layout_name = Gtk.Entry(placeholder_text="Layout name", hexpand=True)
        save = Gtk.Button(label="Save current layout")
        save.add_css_class("suggested-action")

        def do_save(*_a: Any) -> None:
            name = self.layout_name.get_text().strip()
            if name:
                self.engine.save_layout(name)
                self._fill_layouts()

        save.connect("clicked", do_save)
        self.layout_name.connect("activate", do_save)
        save_row.append(self.layout_name)
        save_row.append(save)
        box.append(save_row)
        self.layout_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.layout_list.add_css_class("boxed-list")
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.layout_list)
        box.append(scroller)
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        load = Gtk.Button(label="Load")
        delete = Gtk.Button(label="Delete")
        load.connect("clicked", lambda *_: self._layout_action("load"))
        delete.connect("clicked", lambda *_: self._layout_action("delete"))
        btns.append(delete)
        btns.append(load)
        box.append(btns)
        self._fill_layouts()
        return box

    def _fill_layouts(self) -> None:
        while (row := self.layout_list.get_row_at_index(0)) is not None:
            self.layout_list.remove(row)
        for name in self.engine.list_layouts():
            row = Gtk.ListBoxRow()
            row.set_child(_margins(Gtk.Label(label=name, xalign=0), 8))
            row.layout = name  # type: ignore[attr-defined]
            self.layout_list.append(row)

    def _layout_action(self, what: str) -> None:
        row = self.layout_list.get_selected_row()
        name = getattr(row, "layout", None)
        if not name:
            return
        if what == "load":
            self._do(lambda: self.engine.load_layout(name))
        else:
            from raintux.core.config import layouts_root

            Gio.File.new_for_path(str(layouts_root() / name)).trash(None)
            self._fill_layouts()

    # ================================================================== Settings
    def _settings_page(self) -> Gtk.Widget:
        from raintux.core.config import save_config
        from raintux.gui import desktop

        cfg = self.engine.config
        box = _margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14))

        def switch_row(label: str, active: bool, on_change: Any, note: str = "") -> None:
            row = Gtk.Box(spacing=12)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            text.append(Gtk.Label(label=label, xalign=0))
            if note:
                n = Gtk.Label(label=note, xalign=0, wrap=True)
                n.add_css_class("rt-dim")
                text.append(n)
            sw = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
            sw.connect("notify::active", lambda s, _p: on_change(s.get_active()))
            row.append(text)
            row.append(sw)
            box.append(row)

        switch_row("Start RainTux when you log in", desktop.is_autostart(), desktop.set_autostart)

        def set_tray(on: bool) -> None:
            cfg.ui.tray = on
            save_config(cfg)

        switch_row("Show tray icon", cfg.ui.tray, set_tray, "Takes effect after a restart.")

        def set_api(on: bool) -> None:
            cfg.api.enabled = on
            save_config(cfg)

        switch_row(f"Local API on http://{cfg.api.host}:{cfg.api.port}", cfg.api.enabled, set_api,
                   "Used by the web Skin Manager and scripts. Takes effect after a restart.")

        ed = Gtk.Box(spacing=12)
        ed.append(Gtk.Label(label="Skin editor command", xalign=0, hexpand=True))
        entry = Gtk.Entry(text=cfg.ui.editor, placeholder_text="default app ($VISUAL / $EDITOR)", width_chars=28)

        def set_editor(*_a: Any) -> None:
            cfg.ui.editor = entry.get_text().strip()
            save_config(cfg)

        entry.connect("activate", set_editor)
        entry.connect("notify::has-focus", lambda e, _p: None if e.has_focus() else set_editor())
        ed.append(entry)
        box.append(ed)

        roots = Gtk.Label(label="Skins folders:\n" + "\n".join(str(r) for r in self.engine.roots()), xalign=0,
                          selectable=True, wrap=True)
        roots.add_css_class("rt-dim")
        box.append(roots)

        upd = Gtk.Box(spacing=8)
        status = Gtk.Label(label="", xalign=0, hexpand=True, wrap=True, selectable=True)
        update = Gtk.Button(label="Update from GitHub")
        restart = Gtk.Button(label="Restart RainTux")

        def do_update(*_a: Any) -> None:
            update.set_sensitive(False)
            status.set_text("Updating…")

            def work() -> None:
                ok, out = desktop.update_checkout()
                GLib.idle_add(lambda: (status.set_text(out[-600:]), update.set_sensitive(True), False)[2])

            import threading

            threading.Thread(target=work, daemon=True).start()

        update.connect("clicked", do_update)
        restart.connect("clicked", lambda *_: self.engine.host_bang(next(iter(self.engine.active.values())).skin, "restart", [])
                        if self.engine.active else self.engine._restart())
        upd.append(status)
        upd.append(update)
        upd.append(restart)
        box.append(upd)
        return box

    # ================================================================== Log
    def _log_page(self) -> Gtk.Widget:
        from raintux.gui.log_buffer import RECORDS, add_listener

        box = _margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), 12)
        bar = Gtk.Box(spacing=8)
        self.log_filter = Gtk.DropDown.new_from_strings(["All", "Warnings and errors", "Errors"])
        self.log_filter.connect("notify::selected", lambda *_: self._fill_log())
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", lambda *_: (RECORDS.clear(), self._fill_log()))
        bar.append(Gtk.Label(label="Show", xalign=0))
        bar.append(self.log_filter)
        bar.append(Gtk.Box(hexpand=True))
        bar.append(clear)
        box.append(bar)
        self.log_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.log_view)
        box.append(scroller)
        self._log_listener = lambda entry: GLib.idle_add(lambda: (self._append_log(entry), False)[1])
        add_listener(self._log_listener)
        self._fill_log()
        return box

    def _log_ok(self, level: str) -> bool:
        sel = self.log_filter.get_selected()
        return sel == 0 or (sel == 1 and level in ("WARNING", "ERROR", "CRITICAL")) or (sel == 2 and level in ("ERROR", "CRITICAL"))

    @staticmethod
    def _fmt(entry: tuple[float, str, str, str]) -> str:
        t, level, src, msg = entry
        return f"{time.strftime('%H:%M:%S', time.localtime(t))}  {level:<7} {src}: {msg}\n"

    def _fill_log(self) -> None:
        from raintux.gui.log_buffer import RECORDS

        buf = self.log_view.get_buffer()
        buf.set_text("".join(self._fmt(e) for e in RECORDS if self._log_ok(e[1])))

    def _append_log(self, entry: tuple[float, str, str, str]) -> None:
        if self._log_ok(entry[1]):
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), self._fmt(entry))

    def _detach_log(self) -> None:
        from raintux.gui.log_buffer import remove_listener

        remove_listener(getattr(self, "_log_listener", None))

    # ================================================================== helpers
    def _do(self, fn: Any) -> None:
        try:
            fn()
        except Exception as e:
            log.exception("manage action failed")
            Gtk.AlertDialog(message="Something went wrong", detail=str(e)).show(self)
        GLib.idle_add(lambda: (self.refresh(), False)[1])

    def _choose_package(self) -> None:
        dialog = Gtk.FileDialog(title="Install a Rainmeter skin package")
        flt = Gtk.FileFilter()
        flt.set_name("Rainmeter skins (*.rmskin, *.zip)")
        flt.add_pattern("*.rmskin")
        flt.add_pattern("*.zip")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dialog.set_filters(store)

        def done(d: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f is not None and f.get_path():
                from raintux.gui.installer import install_dialog

                install_dialog(self.engine, Path(f.get_path()), parent=self)

        dialog.open(self, None, done)
