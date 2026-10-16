"""``Measure=CPU`` — processor load in percent."""

from __future__ import annotations

import psutil

from raintux.measures.base_measure import Measure


def _busy_total(t: psutil._common.scputimes) -> tuple[float, float]:  # type: ignore[name-defined]
    idle = t.idle + getattr(t, "iowait", 0.0)
    total = sum(t)
    # guest time is already counted in user/nice on Linux.
    total -= getattr(t, "guest", 0.0) + getattr(t, "guest_nice", 0.0)
    return total - idle, total


class CpuMeasure(Measure):
    """``Processor=0`` (default) is the average of all cores; ``Processor=N`` is core N.

    Each measure keeps its own previous sample, so several CPU measures in one or
    many skins don't disturb each other (unlike ``psutil.cpu_percent``'s shared state).
    """

    default_max = 100.0

    def read_measure(self) -> None:
        self.processor = max(0, self.get_int("processor", 0))
        self._last: tuple[float, float] | None = getattr(self, "_last", None)

    def _sample(self) -> tuple[float, float]:
        if self.processor == 0:
            return _busy_total(psutil.cpu_times())
        per = psutil.cpu_times(percpu=True)
        idx = self.processor - 1
        return _busy_total(per[idx]) if idx < len(per) else (0.0, 0.0)

    def update_value(self) -> None:
        busy, total = self._sample()
        if self._last is not None:
            d_total = total - self._last[1]
            d_busy = busy - self._last[0]
            self._raw_number = max(0.0, min(100.0, d_busy / d_total * 100.0)) if d_total > 0 else 0.0
        self._last = (busy, total)
