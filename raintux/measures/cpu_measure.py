"""CPU utilization via psutil."""

from __future__ import annotations

import psutil

from raintux.measures.base_measure import BaseMeasure


class CpuMeasure(BaseMeasure):
    """``Type=CPU`` — percent busy, non-blocking poll."""

    async def _poll(self) -> float:
        # Non-blocking: percent since the previous call (the very first call returns 0).
        return float(psutil.cpu_percent(interval=None))

    def display_string(self) -> str:
        if self._value is None:
            return "0"
        return f"{float(self._value):.0f}"
