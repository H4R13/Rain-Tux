"""``Measure=Time`` — current or fixed time; number is seconds since 1601-01-01 (Windows epoch)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from raintux.core.formula import parse_number
from raintux.measures.base_measure import Measure

# Seconds between 1601-01-01 and 1970-01-01.
EPOCH_1601 = 11644473600.0


def rainmeter_strftime(fmt: str, dt: datetime) -> str:
    """strftime with Rainmeter/Windows extras: ``%#d`` (no padding), ``locale_date``/``locale_time``."""
    if fmt.strip().lower() == "locale_date":
        return dt.strftime("%x")
    if fmt.strip().lower() == "locale_time":
        return dt.strftime("%X")
    fmt = re.sub(r"%#([a-zA-Z])", r"%-\1", fmt)
    try:
        return dt.strftime(fmt)
    except ValueError:
        # Platforms without %-d: fall back to padded output.
        return dt.strftime(fmt.replace("%-", "%"))


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
                    return datetime.strptime(self.timestamp, self.timestamp_format.replace("%#", "%"))
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
        self._raw_number = (dt - datetime(1601, 1, 1)).total_seconds()
        self.string = rainmeter_strftime(self.fmt, dt)
