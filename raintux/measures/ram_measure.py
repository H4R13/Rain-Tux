"""``Measure=PhysicalMemory`` / ``SwapMemory`` / ``Memory`` — bytes used (``Total=1``: total)."""

from __future__ import annotations

import psutil

from raintux.measures.base_measure import Measure


class RamMeasure(Measure):
    """Rainmeter reports bytes, with MaxValue set to the total, so ``Percentual=1`` works."""

    def read_measure(self) -> None:
        self.mtype = self.get("measure", self.get("type")).strip().lower()
        self.total = self.get_bool("total")

    def _usage(self) -> tuple[float, float]:
        if self.mtype in ("swapmemory", "swap"):
            s = psutil.swap_memory()
            return float(s.used), float(s.total)
        vm = psutil.virtual_memory()
        if self.mtype == "memory":
            # Windows "commit charge" ~ RAM in use plus swap in use.
            s = psutil.swap_memory()
            return float(vm.total - vm.available + s.used), float(vm.total + s.total)
        return float(vm.total - vm.available), float(vm.total)

    def update_value(self) -> None:
        used, total = self._usage()
        if not self.has("maxvalue"):
            self.max_value = total
        self._raw_number = total if self.total else used
