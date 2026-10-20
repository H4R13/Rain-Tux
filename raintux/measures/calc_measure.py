"""``Measure=Calc`` — evaluate ``Formula=`` with measure names, ``Counter`` and ``Random``."""

from __future__ import annotations

import logging
import random

from raintux.core.formula import FormulaError, evaluate
from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)


class CalcMeasure(Measure):
    def read_measure(self) -> None:
        self.formula = self.get("formula", "0")
        self.low = self.get_int("lowbound", 0)
        self.high = self.get_int("highbound", 100)
        self.update_random = self.get_bool("updaterandom")
        self.unique_random = self.get_bool("uniquerandom")
        if not hasattr(self, "_counter"):
            self._counter = 0
            self._random: float | None = None
            self._deck: list[int] = []

    def _next_random(self) -> float:
        lo, hi = sorted((self.low, self.high))
        if self.unique_random and hi - lo < 100000:
            if not self._deck:
                self._deck = list(range(lo, hi + 1))
                random.shuffle(self._deck)
            return float(self._deck.pop())
        return float(random.randint(lo, hi))

    def update_value(self) -> None:
        self._counter += 1
        if self._random is None or self.update_random:
            self._random = self._next_random()

        def resolve(name: str) -> float | None:
            low = name.lower()
            if low == "counter":
                return float(self._counter)
            if low == "random":
                return self._random
            if low == self.name.lower():
                return self.value
            return self.skin.measure_number(name)

        try:
            self._raw_number = evaluate(self.formula, resolve)
        except FormulaError as e:
            # MeasureCalc::UpdateValue: the value stays, the error is logged once until it parses.
            self.report_error("formula", "Formula=%s: %s", self.formula, e)
            return
        self.clear_error("formula")

    def command(self, args: str) -> None:
        log.info("[%s] Calc measures take no commands (%r)", self.name, args)
