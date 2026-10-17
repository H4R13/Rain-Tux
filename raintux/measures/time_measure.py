"""``Measure=Time`` — current or fixed time; number is seconds since 1601-01-01 (Windows epoch)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from raintux.core.formula import parse_number
from raintux.measures.base_measure import Measure

# Seconds between 1601-01-01 and 1970-01-01.
EPOCH_1601 = 11644473600.0
_LEADING_NUMBER = re.compile(r"\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def rainmeter_strftime(fmt: str, dt: datetime) -> str:
    """strftime with Rainmeter/Windows extras: ``%#d`` (no padding), ``locale_date``/``locale_time``."""
    if fmt.strip().lower() in ("locale_date", "locale-date"):
        return dt.strftime("%x")
    if fmt.strip().lower() in ("locale_time", "locale-time"):
        return dt.strftime("%X")
    fmt = re.sub(r"%#([a-zA-Z])", r"%-\1", fmt)
    try:
        return dt.strftime(fmt)
    except ValueError:
        # Platforms without %-d: fall back to padded output.
        return dt.strftime(fmt.replace("%-", "%"))


# C strptime shorthands that Python's strptime lacks (Rainmeter's TimeStampFormat accepts them).
_STRPTIME_SHORTHANDS = {"%D": "%m/%d/%y", "%T": "%H:%M:%S", "%R": "%H:%M", "%F": "%Y-%m-%d",
                        "%r": "%I:%M:%S %p", "%e": "%d", "%h": "%b", "%n": " ", "%t": " "}


def _strptime_format(fmt: str) -> str:
    fmt = fmt.strip().strip('"').replace("%#", "%")
    return re.sub(r"%[DTRFrehnt]", lambda m: _STRPTIME_SHORTHANDS[m.group(0)], fmt)


class TimeMeasure(Measure):
    def read_measure(self) -> None:
        self.fmt = self.get("format", "%H:%M:%S")
        tz = self.get("timezone", "local").strip().lower()
        self.tz_hours: float | None = None if tz in ("", "local") else parse_number(tz, 0.0)
        self.dst = self.get_bool("daylightsavingtime", True)
        self.timestamp = self.get("timestamp").strip()
        self.timestamp_format = self.get("timestampformat")
        self.has_format = self.has("format")

    def _now(self) -> datetime:
        if self.timestamp:
            if self.timestamp_format:
                try:
                    return datetime.strptime(self.timestamp.strip('"'), _strptime_format(self.timestamp_format))
                except ValueError:
                    pass
            try:
                return datetime(1601, 1, 1) + timedelta(seconds=parse_number(self.timestamp, 0.0))
            except OverflowError:
                return datetime(1601, 1, 1)
        if self.tz_hours is None:
            return datetime.now()
        offset = self.tz_hours
        if self.dst and time.localtime().tm_isdst > 0:
            offset += 1
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=offset)

    def update_value(self) -> None:
        dt = self._now()
        self.string = rainmeter_strftime(self.fmt, dt)
        if self.has_format:
            # Rainmeter: with a Format the number is the text's leading number (wcstod),
            # e.g. Format=%#j gives the day of the year; "Mon" gives 0.
            m = _LEADING_NUMBER.match(self.string)
            self._raw_number = float(m.group(0)) if m else 0.0
        else:
            self._raw_number = float(int((dt - datetime(1601, 1, 1)).total_seconds()))  # whole seconds
