"""Small built-in measures: ``String``, ``Uptime``, ``Loop`` and a stub for Windows-only types."""

from __future__ import annotations

import logging
import re
import time

import psutil

from raintux.core.formula import parse_number
from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)


class StringMeasure(Measure):
    """``Measure=String`` — the ``String=`` option (number is its numeric value)."""

    def read_measure(self) -> None:
        # Like Rainmeter, publish the value at once so meters read before the first update
        # can use it (MeasureName=Measure[MeasureDrive]Total).
        if not self.initialized and not self.disabled and not self.paused:
            self.update_value()
            self.value = self._raw_number

    def update_value(self) -> None:
        s = self.get("string")
        self.string = s
        self._raw_number = parse_number(s, 0.0)


_PRINTF = re.compile(r"%(\d)(?:!([^!]*)!)?")


def windows_format(fmt: str, args: list[float]) -> str:
    """Rainmeter's ordered printf (``%1!i!``, ``%2!02i!``) used by Uptime ``Format=``."""

    def repl(m: re.Match[str]) -> str:
        idx = int(m.group(1)) - 1
        spec = m.group(2) or "i"
        v = args[idx] if 0 <= idx < len(args) else 0
        conv, flags = spec[-1], spec[:-1]
        if conv in "iduxX":
            return format(int(v), flags + ("d" if conv in "iu" else conv))
        return format(float(v), flags + "f")

    return _PRINTF.sub(repl, fmt)


class UptimeMeasure(Measure):
    """Seconds since boot; ``Format=`` default ``%4!i!d %3!i!:%2!02i!`` (days, hours, minutes)."""

    def read_measure(self) -> None:
        self.fmt = self.get("format", "%4!i!d %3!i!:%2!02i!")
        self.add_days = self.get_bool("adddaystohours")

    def update_value(self) -> None:
        secs = max(0.0, time.time() - psutil.boot_time())
        self._raw_number = secs
        days, rem = divmod(int(secs), 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        if self.add_days:
            hours += days * 24
            days = 0
        self.string = windows_format(self.fmt, [seconds, minutes, hours, days])


class LoopMeasure(Measure):
    """``Measure=Loop`` — StartValue → EndValue by Increment, LoopCount times (0 = forever)."""

    bang_namespace = "loop"

    def read_measure(self) -> None:
        self.start = self.get_number("startvalue", 0)
        self.end = self.get_number("endvalue", 100)
        self.step = self.get_number("increment", 1) or 1
        self.loop_count = self.get_int("loopcount", 0)
        if not self.has("minvalue"):
            self.min_value = min(self.start, self.end)
        if not self.has("maxvalue"):
            self.max_value = max(self.start, self.end)
        if not hasattr(self, "_loops"):
            self.reset()

    def reset(self) -> None:
        self._cur: float | None = None
        self._loops = 0

    def update_value(self) -> None:
        if self._cur is None:
            self._cur = self.start
        else:
            nxt = self._cur + self.step
            if (nxt > self.end) if self.step > 0 else (nxt < self.end):
                self._loops += 1
                nxt = self._cur if self.loop_count and self._loops >= self.loop_count else self.start
            self._cur = nxt
        self._raw_number = self._cur

    def command(self, args: str) -> None:
        if args.strip().lower() == "reset":
            self.reset()


class RegistryMeasure(Measure):
    """``Measure=Registry`` over the keys :mod:`raintux.compat.windows` answers (CPU name, accent
    colour, date formats, Windows version…); other keys read as missing (0, no string)."""

    def read_measure(self) -> None:
        self.hkey = self.get("reghkey", "HKEY_CURRENT_USER")
        self.key = self.get("regkey")
        self.value_name = self.get("regvalue")
        self.output = self.get("outputtype", "Value").strip().lower()
        self.delimiter = self.get("outputdelimiter", "\n")
        if not self.has("maxvalue") or self.max_value == 0:
            self.max_value = 1.0

    def update_value(self) -> None:
        from raintux.compat.windows import read_key, read_value, subkeys

        self._raw_number, self.string = 0.0, None
        if self.output == "subkeylist":
            self.string = self.delimiter.join(subkeys(self.hkey, self.key)) or None
            return
        if self.output == "valuelist":
            self.string = self.delimiter.join(read_key(self.hkey, self.key) or {}) or None
            return
        found = read_value(self.hkey, self.key, self.value_name)
        if found is None:
            return
        kind, data = found
        if kind in ("REG_DWORD", "REG_QWORD"):
            self._raw_number = float(data)
        elif kind == "REG_BINARY":
            self.string = bytes(data).hex().upper() or None
        else:
            text = self.delimiter.join(data) if kind == "REG_MULTI_SZ" else str(data)
            self.string = text or None
            self._raw_number = parse_number(text.split(self.delimiter)[0] if text else "", 0.0)


class UnsupportedMeasure(Measure):
    """Windows-only measure types (Registry, …): constant 0 / empty string, logged once."""

    def initialize(self) -> None:
        log.info("[%s] measure type %r is not available on Linux", self.name, self.get("measure", self.get("type")))

    def update_value(self) -> None:
        self._raw_number = 0.0
        self.string = ""
