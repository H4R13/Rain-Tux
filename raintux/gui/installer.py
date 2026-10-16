"""Install dialog for ``.rmskin`` packages (also used for ``raintux file.rmskin``)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gi.repository import GLib, Gtk

from raintux.core.rmskin import install_package, open_package

if TYPE_CHECKING:
    from raintux.core.engine import RainTuxEngine

log = logging.getLogger(__name__)


def install_dialog(engine: RainTuxEngine, path: Path, parent: Any = None) -> None:
    try:
        info, zf = open_package(path)
        zf.close()
    except Exception as e:
        Gtk.AlertDialog(message="Not a valid skin package", detail=f"{path.name}: {e}").show(parent)
        return
    win = Gtk.Window(title="Install skin", modal=parent is not None, transient_for=parent)
    win.set_default_size(460, 0)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    for m in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{m}")(18)
    win.set_child(box)
    title = Gtk.Label(label=info.name or path.stem, xalign=0)
    title.add_css_class("title-2")
    box.append(title)
    lines = []
    if info.author:
        lines.append(f"by {info.author}")
    if info.version:
        lines.append(f"Version {info.version}")
    if info.skins:
        lines.append("Skins: " + ", ".join(info.skins))
    if info.layouts:
        lines.append("Layouts: " + ", ".join(info.layouts))
    if info.plugins:
        lines.append("Windows plugins (not usable on Linux, skipped): " + ", ".join(sorted(set(info.plugins))))
    existing = [s for s in info.skins if (engine.primary_root() / s).exists()]
    if existing:
        lines.append("Existing folders are backed up to @Backup: " + ", ".join(existing))
    box.append(Gtk.Label(label="\n".join(lines), xalign=0, wrap=True))
    load_cb = Gtk.CheckButton(label=f"Load {info.load}" if info.load else "Load after installing", active=bool(info.load))
    load_cb.set_sensitive(bool(info.load) and info.load_type.lower() == "skin")
    box.append(load_cb)
    buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda *_: win.destroy())
    ok = Gtk.Button(label="Install")
    ok.add_css_class("suggested-action")

    def do_install(*_a: Any) -> None:
        try:
            result = install_package(path, engine.primary_root(), layouts_root=engine.primary_root().parent / "Layouts")
        except Exception as e:
            log.exception("install failed")
            Gtk.AlertDialog(message="Install failed", detail=str(e)).show(win)
            return
        win.destroy()
        if load_cb.get_active() and result.load:
            parts = result.load.replace("/", "\\").split("\\")
            config, file = "\\".join(parts[:-1]), parts[-1]
            GLib.idle_add(lambda: (engine._safe_activate(config, file), False)[1])
        if engine._manage_window is not None:
            engine._manage_window.refresh(select=result.skins[0] if result.skins else None)

    ok.connect("clicked", do_install)
    buttons.append(cancel)
    buttons.append(ok)
    box.append(buttons)
    win.present()
