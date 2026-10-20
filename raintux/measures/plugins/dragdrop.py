"""``Measure=DragDrop``: react to files dropped on the skin.

``OnDropAction`` (``OnDroppedAction``), ``OnEnterAction``, ``OnOverAction``, ``OnLeaveAction``;
``Action=None|Path|Copy|Move|Delete|Shortcut`` with ``Path``, ``OverrideExisting``,
``ProcessAllFiles``; ``Bounds=MeterName`` or ``Bounds=x,y,w,h``. Action variables:
``$File$``/``$FilePath$``, ``$Name$``/``$FileName$``, ``$Type$``/``$FileType$``,
``$Directory$``/``$FileDirectory$``, ``$FileNr$``, ``$MouseX$``, ``$MouseY$``.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from raintux.core.formula import parse_number
from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)


class DragDropMeasure(Measure):
    def read_measure(self) -> None:
        path = self.get("path")
        self.target_dir = self.skin.path(path) if path else None
        self.on_drop = self.get("ondropaction") or self.get("ondroppedaction")
        self.action = self.get("action", "None").strip().lower() or "none"
        self.process_all = self.get_bool("processallfiles")
        self.override = self.get_bool("overrideexisting")
        bounds = self.get("bounds").strip()
        self.bounds_meter = ""
        self.bounds_rect: tuple[float, float, float, float] | None = None
        if bounds:
            parts = [p.strip() for p in bounds.split(",")]
            if len(parts) == 4:
                vals = [parse_number(p, 0.0, self.skin.measure_number) for p in parts]
                self.bounds_rect = (vals[0], vals[1], vals[2], vals[3])
            else:
                self.bounds_meter = bounds
        if self.action in ("move", "copy", "shortcut") and self.target_dir is None:
            log.warning("[%s] Action=%s needs Path; measure disabled", self.name, self.action)
            self.disabled = True
        if not hasattr(self, "_inside"):
            self._inside = False

    def contains(self, x: float, y: float) -> bool:
        if self.bounds_rect is not None:
            bx, by, bw, bh = self.bounds_rect
        elif self.bounds_meter:
            mt = self.skin.meter(self.bounds_meter)
            if mt is None or mt.hidden:
                return False
            bx, by, bw, bh = mt.box()
        else:
            return 0 <= x < self.skin.width and 0 <= y < self.skin.height
        return bx <= x < bx + bw and by <= y < by + bh

    def update_value(self) -> None:
        self._raw_number = 1.0 if self._inside else 0.0

    def _vars(self, action: str, file: Path, number: int, x: float, y: float) -> str:
        p = str(file)
        repl = {
            "$Name$": file.stem if file.is_file() else file.name, "$FileName$": file.stem if file.is_file() else file.name,
            "$Type$": file.suffix.lstrip(".") if file.is_file() else "folder",
            "$FileType$": file.suffix.lstrip(".") if file.is_file() else "folder",
            "$Directory$": str(file.parent), "$FileDirectory$": str(file.parent),
            "$File$": p, "$FilePath$": p, "$FileNr$": str(number),
            "$MouseX$": str(int(x)), "$MouseY$": str(int(y)),
        }
        for k, v in repl.items():
            action = action.replace(k, v)
        return action

    def drag_motion(self, x: float, y: float) -> bool:
        """Pointer with files over the skin. Returns True when this measure accepts the drop here."""
        if self.disabled:
            return False
        inside = self.contains(x, y)
        if inside and not self._inside:
            self.skin.execute(self.get("onenteraction"), self)
        elif not inside and self._inside:
            self.skin.execute(self.get("onleaveaction"), self)
        elif inside:
            self.skin.execute(self.get("onoveraction"), self)
        self._inside = inside
        return inside

    def drag_leave(self) -> None:
        if self._inside:
            self._inside = False
            self.skin.execute(self.get("onleaveaction"), self)

    def dropped(self, files: list[Path], x: float, y: float) -> bool:
        if self.disabled or not self.contains(x, y):
            return False
        self._inside = False
        for n, f in enumerate(files if self.process_all else files[:1], start=1):
            result = self._apply(f)
            if result is None:
                continue
            self.string = str(result)
            self.skin.execute(self._vars(self.on_drop, result, n, x + self.skin.state.x, y + self.skin.state.y), self)
        return True

    def _apply(self, f: Path) -> Path | None:
        a = self.action
        try:
            if a in ("none", "path"):
                return f
            if a == "delete":
                from gi.repository import Gio  # moved to the trash, never destroyed

                Gio.File.new_for_path(str(f)).trash(None)
                return f
            assert self.target_dir is not None
            self.target_dir.mkdir(parents=True, exist_ok=True)
            dest = self.target_dir / f.name
            if dest.exists() and not self.override:
                log.info("[%s] %s exists (OverrideExisting=0)", self.name, dest)
                return None
            if a == "copy":
                shutil.copytree(f, dest, dirs_exist_ok=True) if f.is_dir() else shutil.copy2(f, dest)
            elif a == "move":
                shutil.move(str(f), str(dest))
            elif a == "shortcut":
                if dest.exists() or dest.is_symlink():
                    dest.unlink()
                os.symlink(f, dest)
            return dest
        except OSError as e:
            log.warning("[%s] drop %s failed: %s", self.name, a, e)
            return None


def drop_measures(skin: Any) -> list[DragDropMeasure]:
    return [m for m in skin.measures.values() if isinstance(m, DragDropMeasure)]
