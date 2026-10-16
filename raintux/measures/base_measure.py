"""Measure base class: common Rainmeter measure options and value formatting."""

from __future__ import annotations

import logging
import math
import re
from collections import deque
from typing import TYPE_CHECKING, Any

from raintux.core.formula import FormulaError, evaluate
from raintux.core.section import Section

if TYPE_CHECKING:
    from raintux.core.skin import Skin

log = logging.getLogger(__name__)

_SCALES = ["", "k", "M", "G", "T", "P"]
_EXECUTOR: Any = None


def _executor() -> Any:
    """Shared worker pool for blocking plugin work (HTTP, subprocesses, D-Bus)."""
    global _EXECUTOR
    if _EXECUTOR is None:
        from concurrent.futures import ThreadPoolExecutor

        _EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="raintux-measure")
    return _EXECUTOR


def format_number(
    value: float,
    decimals: int = 0,
    auto_scale: int = 0,
    scale: float = 1.0,
) -> str:
    """Rainmeter number text: ``Scale``, ``NumOfDecimals`` and ``AutoScale``.

    ``auto_scale``: 1 = 1024, 2 = 1000, 3 = 1024 with at least "k", 4 = 1000 with at least "k".
    """
    if scale and scale != 1:
        value = value / scale
    suffix = ""
    if auto_scale:
        base = 1000.0 if auto_scale in (2, 4) else 1024.0
        i = 0
        while abs(value) >= base and i < len(_SCALES) - 1:
            value /= base
            i += 1
        if i == 0 and auto_scale in (3, 4):
            value /= base
            i = 1
        suffix = " " + _SCALES[i]
    text = f"{value:.{max(0, decimals)}f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]
    return text + suffix


def trim_number(value: float) -> str:
    """Section-variable form of a number: up to 5 decimals, trailing zeros removed."""
    if math.isnan(value) or math.isinf(value):
        return "0"
    if value == int(value):
        return str(int(value))
    return f"{value:.5f}".rstrip("0").rstrip(".")


def _parse_substitute(spec: str) -> list[tuple[str, str]]:
    """``"a":"b","c":"d"`` → pairs (quotes may be ``"`` or ``'``)."""
    return [(m.group(2), m.group(4)) for m in re.finditer(r"""(["'])(.*?)\1\s*:\s*(["'])(.*?)\3""", spec, re.S)]


def pcre_to_python(pattern: str) -> str:
    """Rainmeter patterns are PCRE (``(?siU)``, ``(?<name>…)``); see :mod:`raintux.core.pcre`."""
    from raintux.core.pcre import to_python

    try:
        return to_python(pattern)
    except re.error:
        return pattern


class Measure(Section):
    """A data source. Subclasses implement :meth:`update_value` (and maybe :meth:`read_measure`)."""

    kind = "measure"
    default_min = 0.0
    default_max = 1.0

    def __init__(self, skin: Skin, name: str, raw: dict[str, str]) -> None:
        super().__init__(skin, name, raw)
        self.value = 0.0  # final number value
        self.string: str | None = None  # string value, for measures that have one
        self._raw_number = 0.0
        self.min_value = self.default_min
        self.max_value = self.default_max
        self.invert = False
        self.average_size = 0
        self.substitute: list[tuple[str, str]] = []
        self.regexp_substitute = False
        self.disabled = False
        self.paused = False
        self._average: deque[float] = deque()
        self._prev: tuple[float, str] | None = None
        self._if_state: dict[str, bool] = {}
        self.initialized = False

    # -- options ---------------------------------------------------------------
    def read_options(self) -> None:
        self.min_value = self.get_number("minvalue", self.default_min)
        self.max_value = self.get_number("maxvalue", self.default_max)
        self.invert = self.get_bool("invertmeasure")
        self.average_size = max(0, self.get_int("averagesize", 0))
        if not self.initialized:
            self.disabled = self.get_bool("disabled")
            self.paused = self.get_bool("paused")
        self.substitute = _parse_substitute(self.get("substitute"))
        self.regexp_substitute = self.get_bool("regexpsubstitute")
        self.read_measure()

    def default_update_divider(self) -> int:
        return self.skin.default_update_divider

    def read_measure(self) -> None:
        """Type-specific options."""

    # -- lifecycle ---------------------------------------------------------------
    def initialize(self) -> None:
        """Called once after options are first read."""

    def finalize(self) -> None:
        """Called when the skin unloads (stop threads, close handles)."""

    def update_value(self) -> None:
        """Set ``self._raw_number`` and optionally ``self.string``."""

    def run_in_background(self, work: Any, done: Any) -> None:
        """Run blocking ``work()`` in a worker thread, then ``done(result)`` on the main loop.

        ``done`` receives an Exception instance when ``work`` raised.
        """

        def job() -> None:
            try:
                result = work()
            except Exception as e:  # handed to ``done``
                result = e
            self.skin.host.call_soon(lambda: None if self.skin.closed else done(result))

        _executor().submit(job)

    def command(self, args: str) -> None:
        """``!CommandMeasure`` target."""
        log.info("[%s] does not support !CommandMeasure %r", self.name, args)

    def update_now(self) -> bool:
        """``!UpdateMeasure``: update regardless of UpdateDivider."""
        return self.update(force=True)

    def update(self, force: bool = False) -> bool:
        """One skin tick. Returns True when the measure actually updated."""
        if self.needs_read or self.dynamic:
            self.read()
        if not self.initialized:
            self.initialized = True
            self.initialize()
        if self.disabled:
            return False
        if not force and (not self.due() or self.paused):
            return False
        try:
            self.update_value()
        except Exception:
            log.exception("measure [%s] update failed", self.name)
            return False
        v = self._raw_number
        if self.average_size > 1:
            self._average.append(v)
            while len(self._average) > self.average_size:
                self._average.popleft()
            v = sum(self._average) / len(self._average)
        if self.invert:
            v = self.max_value - (v - self.min_value)
        self.value = v
        self.after_update()
        return True

    def after_update(self) -> None:
        """Run OnUpdateAction, OnChangeAction and the If… actions."""
        self.skin.execute(self.get("onupdateaction"), self)
        cur = (self.value, self.string_value())
        if self._prev is not None and cur != self._prev:
            self.skin.execute(self.get("onchangeaction"), self)
        self._prev = cur
        self._if_actions()

    # -- values ----------------------------------------------------------------
    def relative(self) -> float:
        """Value in 0..1 within MinValue..MaxValue (for bars, gauges)."""
        rng = self.max_value - self.min_value
        if rng == 0:
            return 0.0
        return max(0.0, min(1.0, (self.value - self.min_value) / rng))

    def apply_substitute(self, text: str) -> str:
        for pat, rep in self.substitute:
            if self.regexp_substitute:
                try:
                    text = re.sub(pcre_to_python(pat), re.sub(r"\\(\d)", r"\\g<\1>", rep), text)
                except re.error as e:
                    log.warning("[%s] bad RegExpSubstitute %r: %s", self.name, pat, e)
            elif pat:
                text = text.replace(pat, rep)
            elif not text:
                text = rep
        return text

    def string_value(
        self,
        decimals: int | None = None,
        auto_scale: int = 0,
        scale: float = 1.0,
        percentual: bool = False,
    ) -> str:
        """Text for meters and ``[Section]`` variables (Substitute applied)."""
        if self.string is not None and not percentual:
            return self.apply_substitute(self.string)
        v = self.relative() * 100.0 if percentual else self.value
        if decimals is None and not auto_scale and scale == 1:
            text = trim_number(v)
        else:
            d = decimals if decimals is not None else (1 if auto_scale else 0)
            text = format_number(v, d, auto_scale, scale)
        return self.apply_substitute(text)

    # -- if actions ------------------------------------------------------------
    def _fire(self, key: str, cond: bool, on_true: str, on_false: str = "", every: bool = False) -> None:
        if self._if_state.get(key) == cond and not every:
            return
        self._if_state[key] = cond
        act = on_true if cond else on_false
        if act:
            self.skin.execute(act, self)

    def _if_actions(self) -> None:
        v = self.value
        if self.has("ifabovevalue"):
            self._fire("above", v > self.get_number("ifabovevalue"), self.get("ifaboveaction"))
        if self.has("ifbelowvalue"):
            self._fire("below", v < self.get_number("ifbelowvalue"), self.get("ifbelowaction"))
        if self.has("ifequalvalue"):
            self._fire("equal", int(v) == int(self.get_number("ifequalvalue")), self.get("ifequalaction"))

        every = self.get_bool("ifconditionmode")
        i = 1
        while (src := self.opts.get("ifcondition" + ("" if i == 1 else str(i)))) is not None:
            sfx = "" if i == 1 else str(i)
            try:
                cond = evaluate(self.skin.resolve(src, section_vars=True), self.skin.measure_number) != 0
            except FormulaError as e:
                log.warning("[%s] IfCondition%s: %s", self.name, sfx, e)
                cond = False
            self._fire(f"cond{i}", cond, self.get(f"iftrueaction{sfx}"), self.get(f"iffalseaction{sfx}"), every)
            i += 1

        every = self.get_bool("ifmatchmode")
        text = self.string_value()
        i = 1
        while (pat := self.opts.get("ifmatch" + ("" if i == 1 else str(i)))) is not None:
            sfx = "" if i == 1 else str(i)
            try:
                matched = re.search(pcre_to_python(pat), text) is not None
            except re.error as e:
                log.warning("[%s] IfMatch%s: %s", self.name, sfx, e)
                matched = False
            self._fire(f"match{i}", matched, self.get(f"ifmatchaction{sfx}"), self.get(f"ifnotmatchaction{sfx}"), every)
            i += 1
