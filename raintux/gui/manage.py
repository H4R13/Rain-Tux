"""Native "Manage" window: browse configs, load/unload/refresh, per-skin window settings."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gi.repository import Gio, GLib, Gtk, Pango

from raintux.gui.context_menu import POSITIONS, TOGGLES

if TYPE_CHECKING:
    from raintux.core.engine import RainTuxEngine

log = logging.getLogger(__name__)

GET_SKINS_URL = "https://www.deviantart.com/rainmeter"

_CSS = """
.rt-sidebar row { padding: 6px 10px; }
.rt-sidebar row.rt-group { font-weight: 700; padding-top: 12px; opacity: 0.8; }
.rt-active { color: @accent_color; font-weight: 600; }
.rt-title { font-size: 18pt; font-weight: 700; }
.rt-dim { opacity: 0.7; }
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


class ManageWindow(Gtk.ApplicationWindow):
    def __init__(self, engine: RainTuxEngine) -> None:
        super().__init__(application=engine.app, title="RainTux — Manage")
        self.engine = engine
        self.set_default_size(900, 600)
        css = Gtk.CssProvider()
        css.load_from_string(_CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        header = Gtk.HeaderBar()
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

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=300)
        self.set_child(paned)
        self.list = Gtk.ListBox()
        self.list.add_css_class("rt-sidebar")
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-selected", self._on_select)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.list)
        paned.set_start_child(scroller)

        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(self.detail, f"set_margin_{m}")(18)
        dscroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        dscroll.set_child(self.detail)
        paned.set_end_child(dscroll)
        self.selected: str | None = None
        self._updating = False

    # ------------------------------------------------------------------ list
    def refresh(self, select: str | None = None) -> None:
        if select:
            self.selected = select
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        configs = self.engine.list_configs()
        last_group = None
        select_row = None
        for config in configs:
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

    def _on_select(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        cfg = getattr(row, "config", None) if row is not None else None
        if cfg is not None:
            self.selected = cfg
        self._show(cfg)

    # ------------------------------------------------------------------ details
    def _clear(self) -> None:
        while (child := self.detail.get_first_child()) is not None:
            self.detail.remove(child)

    def _show(self, config: str | None) -> None:
        self._clear()
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
                v = Gtk.Label(label=meta[key], xalign=0, selectable=True, wrap=True)
                grid.attach(v, 1, r, 1, 1)
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
            if not self._updating:
                act.window.move_to(int(xs.get_value()), int(ys.get_value()))
                eng.window_moved(act.window)

        xs.connect("value-changed", moved)
        ys.connect("value-changed", moved)
        sg.attach(Gtk.Label(label="Coordinates", xalign=0), 0, 0, 1, 1)
        coords = Gtk.Box(spacing=6)
        coords.append(xs)
        coords.append(ys)
        sg.attach(coords, 1, 0, 1, 1)

        pos = Gtk.DropDown.new_from_strings([label for _z, label in POSITIONS])
        pos.set_selected([z for z, _l in POSITIONS].index(st.z) if st.z in [z for z, _l in POSITIONS] else 2)

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

        def set_order(*_a: Any) -> None:
            cs.load_order = int(order.get_value())
            eng.schedule_save()

        order.connect("value-changed", set_order)
        sg.attach(Gtk.Label(label="Load order", xalign=0), 0, 3, 1, 1)
        sg.attach(order, 1, 3, 1, 1)
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

    def _do(self, fn: Any) -> None:
        try:
            fn()
        except Exception as e:
            log.exception("manage action failed")
            self._error(str(e))
        GLib.idle_add(lambda: (self.refresh(), False)[1])

    def _error(self, text: str) -> None:
        dlg = Gtk.AlertDialog(message="Something went wrong", detail=text)
        dlg.show(self)

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
