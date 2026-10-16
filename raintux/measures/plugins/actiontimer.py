"""``Plugin=ActionTimer``: timed action lists for animations.

``ActionListN=Action1 | Wait 100 | Repeat Action2, 20, 10``; started with
``!CommandMeasure Measure "Execute N"`` and stopped with ``"Stop N"``. Action
options are re-read every time they run, so ``#Variables#`` are always current.
"""

from __future__ import annotations

import logging
from typing import Any

from raintux.core.formula import parse_number
from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)


class _Run:
    def __init__(self, steps: list[tuple[str, Any]]) -> None:
        self.steps = steps
        self.index = 0
        self.repeat_left = 0
        self.handle: Any = None
        self.cancelled = False


class ActionTimerPlugin(Measure):
    bang_namespace = "actiontimer"

    def read_measure(self) -> None:
        if not hasattr(self, "_runs"):
            self._runs: dict[int, _Run] = {}

    def update_value(self) -> None:
        self._raw_number = float(len([r for r in self._runs.values() if not r.cancelled]))

    def _steps(self, n: int) -> list[tuple[str, Any]]:
        spec = self.get(f"actionlist{n}")
        steps: list[tuple[str, Any]] = []
        for raw in spec.split("|"):
            item = raw.strip()
            if not item:
                continue
            word, _sep, rest = item.partition(" ")
            w = word.lower()
            if w == "wait":
                steps.append(("wait", max(0, int(parse_number(rest, 0, self.skin.measure_number)))))
            elif w == "repeat":
                parts = [p.strip() for p in rest.split(",")]
                name = parts[0] if parts else ""
                wait = int(parse_number(parts[1], 0)) if len(parts) > 1 else 0
                count = int(parse_number(parts[2], 1)) if len(parts) > 2 else 1
                steps.append(("repeat", (name, max(0, wait), max(0, count))))
            else:
                steps.append(("action", item))
        return steps

    def _run_action(self, name: str) -> None:
        raw = self.option_raw(name)
        if raw is None:
            if not self.get_bool("ignorewarnings"):
                log.warning("[%s] ActionTimer: option %r not found", self.name, name)
            return
        self.skin.execute(self.skin.resolve(raw), self)

    def _advance(self, n: int, run: _Run) -> None:
        if run.cancelled or self.skin.closed:
            return
        host = self.skin.host
        while run.index < len(run.steps):
            kind, val = run.steps[run.index]
            if kind == "action":
                run.index += 1
                self._run_action(val)
                if run.cancelled:
                    return
            elif kind == "wait":
                run.index += 1
                run.handle = host.call_later(val, lambda: self._advance(n, run))
                return
            else:  # repeat
                name, wait, count = val
                if run.repeat_left == 0:
                    run.repeat_left = count
                if run.repeat_left <= 0:
                    run.index += 1
                    continue
                run.repeat_left -= 1
                self._run_action(name)
                if run.cancelled:
                    return
                if run.repeat_left == 0:
                    run.index += 1
                    run.repeat_left = 0
                    if wait > 0:
                        run.handle = host.call_later(wait, lambda: self._advance(n, run))
                        return
                    continue
                run.handle = host.call_later(wait, lambda: self._advance(n, run))
                return
        if self._runs.get(n) is run:
            del self._runs[n]

    def _stop(self, n: int) -> None:
        run = self._runs.pop(n, None)
        if run is not None:
            run.cancelled = True
            self.skin.host.cancel(run.handle)

    def command(self, args: str) -> None:
        for part in args.split(";"):
            word, _sep, num = part.strip().partition(" ")
            w = word.lower()
            try:
                n = int(parse_number(num, 0))
            except ValueError:
                n = 0
            if w == "execute" and n > 0:
                self._stop(n)  # re-executing a running list restarts it
                run = _Run(self._steps(n))
                self._runs[n] = run
                self._advance(n, run)
            elif w == "stop" and n > 0:
                self._stop(n)
            elif w in ("stopall", "stop") and n == 0:
                for k in list(self._runs):
                    self._stop(k)
            else:
                log.info("[%s] ActionTimer: unknown command %r", self.name, part)

    def finalize(self) -> None:
        for k in list(getattr(self, "_runs", {})):
            self._stop(k)
