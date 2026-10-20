"""File plugins: FolderInfo, QuotePlugin, RecycleManager and FileView."""

from __future__ import annotations

import fnmatch
import logging
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from raintux.core.pcre import compile_pcre
from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)


def _hidden(p: Path) -> bool:
    return p.name.startswith(".")


# --------------------------------------------------------------------------- FolderInfo
class FolderInfoPlugin(Measure):
    """``InfoType=FolderSize|FileCount|FolderCount`` for ``Folder`` (async, every 10 updates)."""

    def read_measure(self) -> None:
        self.folder = self.skin.path(self.get("folder")) if self.get("folder") else None
        self.info = self.get("infotype", "FolderSize").strip().lower()
        self.subfolders = self.get_bool("includesubfolders")
        self.hidden = self.get_bool("includehiddenfiles")
        self.regexp = self.get("regexpfilter")
        if not hasattr(self, "_tick"):
            self._tick = 0
            self._busy = False

    def update_value(self) -> None:
        if self._tick % 10 == 0 and not self._busy and self.folder is not None:
            self._busy = True
            folder, info, sub, hidden = self.folder, self.info, self.subfolders, self.hidden
            rx = compile_pcre(self.regexp) if self.regexp else None

            def work() -> float:
                size = files = dirs = 0
                walker = os.walk(folder) if sub else [(str(folder), *_split(folder))]
                for root, dnames, fnames in walker:
                    if not hidden:
                        dnames[:] = [d for d in dnames if not d.startswith(".")]
                    dirs += len(dnames)
                    for f in fnames:
                        if (not hidden and f.startswith(".")) or (rx and not rx.search(f)):
                            continue
                        files += 1
                        try:
                            size += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                return float({"filecount": files, "foldercount": dirs}.get(info, size))

            def done(result: Any) -> None:
                self._busy = False
                if not isinstance(result, Exception):
                    self._raw_number = result
                    self.value = result

            self.run_in_background(work, done)
        self._tick += 1


def _split(folder: Path) -> tuple[list[str], list[str]]:
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return [], []
    return [e.name for e in entries if e.is_dir()], [e.name for e in entries if e.is_file()]


# --------------------------------------------------------------------------- QuotePlugin
class QuotePlugin(Measure):
    """A random quote from a file (``Separator``) or a random file from a folder (``FileFilter``)."""

    def read_measure(self) -> None:
        self.path = self.skin.path(self.get("pathname")) if self.get("pathname") else None
        self.separator = self.get("separator", "\n").replace("\\n", "\n") or "\n"
        self.filters = [f.strip() for f in self.get("filefilter").split(";") if f.strip()]
        self.subfolders = self.get_bool("subfolders")
        if not hasattr(self, "_items"):
            self._items: list[str] = []
            self._loaded_from: Path | None = None

    def _load(self) -> None:
        if self.path is None or self._loaded_from == self.path:
            return
        self._loaded_from = self.path
        if self.path.is_dir():
            it = self.path.rglob("*") if self.subfolders else self.path.iterdir()
            self._items = sorted(str(p) for p in it if p.is_file() and (
                not self.filters or any(fnmatch.fnmatch(p.name.lower(), f.lower()) for f in self.filters)))
        elif self.path.is_file():
            raw = self.path.read_bytes()
            from raintux.core.ini_writer import detect_encoding

            text = raw.decode(detect_encoding(raw), errors="replace").replace("\r\n", "\n")
            self._items = [q.strip() for q in text.split(self.separator) if q.strip()]

    def update_value(self) -> None:
        self._load()
        self.string = random.choice(self._items) if self._items else ""


# --------------------------------------------------------------------------- RecycleManager
def trash_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "Trash"


class RecycleManagerPlugin(Measure):
    """``RecycleType=Count|Size`` of the desktop trash; commands EmptyBin, EmptyBinSilent, OpenBin."""

    bang_namespace = "recyclemanager"

    def read_measure(self) -> None:
        self.kind = self.get("recycletype", "Count").strip().lower()
        if not hasattr(self, "_tick"):
            self._tick = 0

    def update_value(self) -> None:
        if self._tick % 5 == 0:
            files = trash_dir() / "files"
            entries = list(files.iterdir()) if files.is_dir() else []
            if self.kind == "size":
                total = 0
                for e in entries:
                    if e.is_dir() and not e.is_symlink():
                        for root, _d, fs in os.walk(e):
                            total += sum(os.path.getsize(os.path.join(root, f)) for f in fs
                                         if os.path.exists(os.path.join(root, f)))
                    elif e.exists():
                        total += e.lstat().st_size
                self._raw_number = float(total)
            else:
                self._raw_number = float(len(entries))
        self._tick += 1

    def command(self, args: str) -> None:
        cmd = args.strip().lower()
        if cmd == "emptybinsilent":
            self._empty()
        elif cmd == "emptybin":
            # Like SHEmptyRecycleBin without SHERB_NOCONFIRMATION: ask before deleting for good.
            files = trash_dir() / "files"
            count = sum(1 for _ in files.iterdir()) if files.is_dir() else 0
            if count == 0:
                return
            confirm = getattr(self.skin.host, "confirm", None)
            if confirm is None:
                log.warning("[%s] EmptyBin needs a confirmation dialog; the trash was left as it is", self.name)
                return
            what = "this item" if count == 1 else f"these {count} items"
            confirm(self.skin, "Empty Trash", f"Are you sure you want to permanently delete {what}?", "Empty Trash",
                    lambda ok: self._empty() if ok and not self.skin.closed else None)
        elif cmd == "openbin":
            from gi.repository import Gio

            Gio.AppInfo.launch_default_for_uri("trash:///", None)
        else:
            super().command(args)

    def _empty(self) -> None:
        empty_trash()
        self._tick = 0


def empty_trash() -> None:
    """Delete everything in the desktop trash, for good."""
    if shutil.which("gio"):
        subprocess.Popen(["gio", "trash", "--empty"], start_new_session=True)
        return
    for sub in ("files", "info"):
        d = trash_dir() / sub
        if d.is_dir():
            for e in d.iterdir():
                shutil.rmtree(e, ignore_errors=True) if e.is_dir() and not e.is_symlink() else e.unlink(missing_ok=True)


# --------------------------------------------------------------------------- FileView
class FileViewPlugin(Measure):
    """Folder browser. Parent: ``Path``, ``Count``, ``Show*``, ``Sort*``, ``Extensions``…
    Child (``Path=[Parent]``): ``Type`` + ``Index``. Commands: ``PageUp``, ``PageDown``,
    ``IndexUp``, ``IndexDown``, ``FollowN``, ``ExecuteN``, ``PreviousFolder``."""

    def read_measure(self) -> None:
        raw = self.skin.resolve(self.option_raw("path") or "").strip()
        m = re.match(r"^\[([^\[\]]+)\]$", raw)
        self.parent_name = m.group(1) if m else None
        self.mtype = self.get("type", "FolderPath").strip().lower()
        self.index = self.get_int("index", 1)
        if self.parent_name is None:
            if not hasattr(self, "folder"):
                self.folder = self.skin.path(raw) if raw else Path.home()
                self.offset = 0
                self.entries: list[Path] = []
                self._scanned = 0.0
            self.count = max(1, self.get_int("count", 1))
            self.recursive = self.get_int("recursive", 0)
            self.show_dotdot = self.get_bool("showdotdot", True)
            self.show_file = self.get_bool("showfile", True)
            self.show_folder = self.get_bool("showfolder", True)
            self.show_hidden = self.get_bool("showhidden")
            self.sort = self.get("sorttype", "Name").strip().lower()
            self.ascending = self.get_bool("sortascending", True)
            self.extensions = {e.strip().lower().lstrip(".") for e in self.get("extensions").split(";") if e.strip()}
            self.wildcard = self.get("wildcardsearch", "*") or "*"
            self.hide_ext = self.get_bool("hideextensions")

    # -- parent ---------------------------------------------------------------
    def _parent(self) -> FileViewPlugin | None:
        if not self.parent_name:
            return self
        m = self.skin.measure(self.parent_name)
        return m if isinstance(m, FileViewPlugin) and m.parent_name is None else None

    def scan(self) -> None:
        items: list[Path] = []
        try:
            it = self.folder.rglob("*") if self.recursive == 2 else self.folder.iterdir()
            for p in it:
                if not self.show_hidden and _hidden(p):
                    continue
                if p.is_dir():
                    if self.show_folder and self.recursive != 2:
                        items.append(p)
                elif self.show_file:
                    if self.extensions and p.suffix.lower().lstrip(".") not in self.extensions:
                        continue
                    if not fnmatch.fnmatch(p.name.lower(), self.wildcard.lower()):
                        continue
                    items.append(p)
        except OSError:
            log.info("[%s] FileView: cannot read %s", self.name, self.folder)

        def key(p: Path) -> Any:
            if self.sort == "size":
                return p.stat().st_size if p.is_file() else -1
            if self.sort == "date":
                return p.stat().st_mtime
            if self.sort == "type":
                return (p.is_file(), p.suffix.lower(), p.name.lower())
            return (p.is_file(), p.name.lower())

        items.sort(key=key, reverse=not self.ascending)
        if self.show_dotdot and self.folder.parent != self.folder:
            items.insert(0, self.folder / "..")
        self.entries = items
        self._scanned = time.monotonic()

    def entry(self, index: int) -> Path | None:
        i = self.offset + index - 1
        return self.entries[i] if 0 <= i < len(self.entries) else None

    def update_value(self) -> None:
        parent = self._parent()
        if parent is None:
            return
        if parent is self:
            if not self.entries or time.monotonic() - self._scanned > 5:
                self.scan()
        self._raw_number, self.string = self._describe(parent)

    def _describe(self, parent: FileViewPlugin) -> tuple[float, str | None]:
        t = self.mtype
        if t == "folderpath":
            return 0.0, str(parent.folder)
        if t == "filecount":
            return float(sum(1 for e in parent.entries if e.is_file())), None
        if t == "foldercount":
            return float(sum(1 for e in parent.entries if e.is_dir() and e.name != "..")), None
        if t == "foldersize":
            return float(sum(e.stat().st_size for e in parent.entries if e.is_file())), None
        e = parent.entry(self.index)
        if e is None:
            return 0.0, ""
        if t == "filename":
            name = ".." if e.name == ".." else (e.stem if parent.hide_ext and e.is_file() else e.name)
            return 0.0, name
        if t == "filepath":
            return 0.0, str(e.resolve())
        if t == "filetype":
            return 0.0, "Folder" if e.is_dir() else (e.suffix.lstrip(".").upper() + " File")
        if t == "filesize":
            return (float(e.stat().st_size) if e.is_file() else 0.0), None
        if t == "filedate":
            return 0.0, time.strftime("%Y-%m-%d %H:%M", time.localtime(e.stat().st_mtime))
        if t == "icon":
            from raintux.gui.icons import icon_png_for

            return 0.0, icon_png_for(e)
        return 0.0, ""

    def command(self, args: str) -> None:
        parent = self._parent()
        if parent is None:
            return
        cmd = args.strip()
        low = cmd.lower()
        if low == "pageup":
            parent.offset = max(0, parent.offset - parent.count)
        elif low == "pagedown":
            if parent.offset + parent.count < len(parent.entries):
                parent.offset += parent.count
        elif low == "indexup":
            parent.offset = max(0, parent.offset - 1)
        elif low == "indexdown":
            if parent.offset + parent.count < len(parent.entries):
                parent.offset += 1
        elif low in ("previousfolder", "parentfolder"):
            parent._go(parent.folder.parent)
        elif low.startswith(("follow", "execute")):
            m = re.match(r"(follow|execute)\s*(\d*)", low)
            idx = int(m.group(2)) if m and m.group(2) else self.index
            e = parent.entry(idx)
            if e is None:
                return
            if e.name == "..":
                parent._go(parent.folder.parent)
            elif e.is_dir() and low.startswith("follow"):
                parent._go(e)
            else:
                from gi.repository import Gio

                Gio.AppInfo.launch_default_for_uri(e.resolve().as_uri(), None)
        elif low.startswith("setpath"):
            parent._go(self.skin.path(cmd[7:].strip()))
        else:
            super().command(args)

    def _go(self, folder: Path) -> None:
        self.folder = folder.resolve()
        self.offset = 0
        self.scan()
        self.skin.execute(self.get("finishaction"), self)
