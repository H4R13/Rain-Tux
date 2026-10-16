"""In-memory log of recent messages for the Manage window's Log tab (Rainmeter's About → Log)."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable

RECORDS: deque[tuple[float, str, str, str]] = deque(maxlen=2000)  # time, level, source, message
_listeners: list[Callable[[tuple[float, str, str, str]], None]] = []


class _Handler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = (time.time(), record.levelname, record.name.replace("raintux.", ""), record.getMessage())
        except Exception:
            return
        RECORDS.append(entry)
        for fn in list(_listeners):
            try:
                fn(entry)
            except Exception:
                pass


_installed = False


def install_log_buffer() -> None:
    global _installed
    if _installed:
        return
    h = _Handler(level=logging.INFO)
    logging.getLogger("raintux").addHandler(h)
    _installed = True


def add_listener(fn: Callable[[tuple[float, str, str, str]], None]) -> None:
    _listeners.append(fn)


def remove_listener(fn: Callable[[tuple[float, str, str, str]], None]) -> None:
    if fn in _listeners:
        _listeners.remove(fn)
