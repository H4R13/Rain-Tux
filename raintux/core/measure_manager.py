"""Constructs and schedules :class:`~raintux.measures.base_measure.BaseMeasure` instances."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from raintux.core.event_bus import EventBus
from raintux.core.skin_parser import ParsedSkin, substitute_variables
from raintux.measures.base_measure import BaseMeasure
from raintux.measures.cpu_measure import CpuMeasure
from raintux.measures.disk_measure import DiskMeasure
from raintux.measures.ram_measure import RamMeasure
from raintux.measures.time_measure import TimeMeasure

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_MEASURE_TYPES: dict[str, type[BaseMeasure]] = {
    "cpu": CpuMeasure,
    "ram": RamMeasure,
    "memory": RamMeasure,
    "physicalmemory": RamMeasure,
    "swap": RamMeasure,
    "disk": DiskMeasure,
    "freedisk": DiskMeasure,
    "time": TimeMeasure,
    "uptime": TimeMeasure,
}


class MeasureManager:
    """Owns measure instances per loaded skin; runs async polling loops."""

    def __init__(self, event_bus: EventBus, default_interval_ms: int = 1000) -> None:
        self._event_bus = event_bus
        self._default_interval_ms = default_interval_ms
        self._skins: dict[str, dict[str, BaseMeasure]] = {}
        self._tasks: dict[str, list[asyncio.Task[Any]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def clear(self) -> None:
        """Stop all tasks and drop every skin's measures."""
        for sid in list(self._tasks.keys()):
            self.clear_skin(sid)
        self._skins.clear()

    def clear_skin(self, skin_id: str) -> None:
        """Cancel polling for one skin."""
        for t in self._tasks.pop(skin_id, []):
            t.cancel()
        self._skins.pop(skin_id, None)

    def load_skin_measures_threadsafe(
        self,
        skin_id: str,
        parsed: ParsedSkin,
        notify: Callable[[], None],
    ) -> None:
        """Schedule (re)build of measures for ``skin_id`` on the asyncio loop."""
        if self._loop is None:
            raise RuntimeError("MeasureManager loop not attached")

        def _go() -> None:
            self.clear_skin(skin_id)
            self._start_tasks_for_skin(skin_id, parsed, notify)

        self._loop.call_soon_threadsafe(_go)

    def _start_tasks_for_skin(
        self,
        skin_id: str,
        parsed: ParsedSkin,
        notify: Callable[[], None],
    ) -> None:
        assert self._loop is not None
        measures: dict[str, BaseMeasure] = {}
        for name, section in parsed.measures.items():
            cf = {k.lower(): v for k, v in section.items()}
            mtype = substitute_variables(cf.get("type", ""), parsed.variables, parsed.root).strip()
            mtype_key = mtype.split(":", 1)[0].strip().lower()
            cls = _MEASURE_TYPES.get(mtype_key)
            if cls is None:
                log.warning("unsupported measure type %s [%s]", mtype, name)
                continue
            interval = self._parse_interval(section, parsed)
            measure = cls(name=name, section=section, skin_root=parsed.root, interval_ms=interval)
            measures[name] = measure

        self._skins[skin_id] = measures

        async def _poll(m: BaseMeasure) -> None:
            while True:
                try:
                    await m.update()
                    await self._event_bus.publish(f"measure:{skin_id}:{m.name}", m.value)
                    notify()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("measure %s:%s failed", skin_id, m.name)
                await asyncio.sleep(m.interval_ms / 1000.0)

        tasks: list[asyncio.Task[Any]] = []
        for m in measures.values():
            tasks.append(self._loop.create_task(_poll(m)))
        self._tasks[skin_id] = tasks

    def get_display_value(self, skin_id: str, name: str) -> str:
        m = self._skins.get(skin_id, {}).get(name)
        if not m:
            return ""
        return m.display_string()

    def _skin_update_ms(self, parsed: ParsedSkin) -> int:
        """``[Rainmeter] Update=`` in milliseconds (configparser lower-cases keys)."""
        raw = {k.lower(): v for k, v in parsed.rainmeter.items()}.get("update")
        if raw:
            try:
                return max(50, int(float(substitute_variables(raw, parsed.variables, parsed.root))))
            except ValueError:
                log.warning("bad Update=%r in %s", raw, parsed.ini_path)
        return self._default_interval_ms

    def _parse_interval(self, section: dict[str, str], parsed: ParsedSkin) -> int:
        raw = {k.lower(): v for k, v in section.items()}
        if "updaterate" in raw:
            try:
                ms = int(float(substitute_variables(raw["updaterate"], parsed.variables, parsed.root)))
                return max(50, ms)
            except ValueError:
                pass
        base = self._skin_update_ms(parsed)
        if "updatedivider" in raw:
            try:
                div = int(substitute_variables(raw["updatedivider"], parsed.variables, parsed.root))
                return max(50, base * max(1, div))
            except ValueError:
                pass
        return base
