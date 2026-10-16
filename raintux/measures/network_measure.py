"""``Measure=NetIn`` / ``NetOut`` / ``NetTotal`` — bytes per second (or cumulative bytes)."""

from __future__ import annotations

import time

import psutil

from raintux.measures.base_measure import Measure


def _interfaces() -> list[str]:
    """Non-loopback interfaces in a stable order (``Interface=1`` is the first)."""
    stats = psutil.net_if_stats()
    return sorted(n for n in stats if n != "lo" and not n.startswith(("lo:", "docker", "veth", "br-", "virbr")))


class NetworkMeasure(Measure):
    """``Interface=0`` sums every real interface; ``Interface=N`` or ``Interface=eth0`` picks one.

    With no ``MaxValue`` the maximum follows the highest rate seen, like Rainmeter.
    """

    def read_measure(self) -> None:
        self.mtype = self.get("measure", self.get("type")).strip().lower()
        self.interface = self.get("interface", "0").strip()
        self.cumulative = self.get_bool("cumulative")
        self.auto_max = not self.has("maxvalue") or self.get_number("maxvalue") == 0
        if not hasattr(self, "_last"):
            self._last: tuple[float, float] | None = None
            self._seen_max = 1.0

    def _bytes(self) -> float:
        counters = psutil.net_io_counters(pernic=True)
        if self.interface.isdigit():
            idx = int(self.interface)
            names = _interfaces()
            names = names if idx == 0 else names[idx - 1 : idx]
        else:
            names = [self.interface]
        total = 0.0
        for n in names:
            c = counters.get(n)
            if c is None:
                continue
            if self.mtype in ("netin", "netinmeasure"):
                total += c.bytes_recv
            elif self.mtype in ("netout", "netoutmeasure"):
                total += c.bytes_sent
            else:
                total += c.bytes_recv + c.bytes_sent
        return total

    def update_value(self) -> None:
        now = time.monotonic()
        b = self._bytes()
        if self.cumulative:
            self._raw_number = b
        elif self._last is not None:
            dt = now - self._last[0]
            self._raw_number = max(0.0, (b - self._last[1]) / dt) if dt > 0 else 0.0
        self._last = (now, b)
        if self.auto_max:
            self._seen_max = max(self._seen_max, self._raw_number)
            self.max_value = self._seen_max
