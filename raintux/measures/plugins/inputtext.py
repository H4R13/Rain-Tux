"""``Plugin=InputText``: an inline text box; ``$UserInput$`` is substituted into ``CommandN``.

``!CommandMeasure MeasureInput "ExecuteBatch 1"`` (also ``1-3`` or ``ALL``). Each
``CommandN`` may override options after its bangs, e.g.
``Command1=[!SetVariable Q "$UserInput$"] DefaultValue="search" X=20 W=200``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from raintux.core.section import parse_color
from raintux.measures.base_measure import Measure

log = logging.getLogger(__name__)

_OVERRIDE = re.compile(r'(\w+)\s*=\s*("([^"]*)"|\S+)')


def split_command(spec: str) -> tuple[str, dict[str, str]]:
    """``[!Bang …][…] Key="v" Key2=v`` → (bangs, overrides)."""
    s = spec.strip()
    depth = 0
    end = -1
    quote = False
    for i, ch in enumerate(s):
        if ch == '"':
            quote = not quote
        elif not quote and ch == "[":
            depth += 1
        elif not quote and ch == "]":
            depth -= 1
            if depth == 0:
                end = i
    if end < 0:
        return s, {}
    overrides = {m.group(1).lower(): (m.group(3) if m.group(3) is not None else m.group(2))
                 for m in _OVERRIDE.finditer(s[end + 1:])}
    return s[: end + 1], overrides


class InputTextPlugin(Measure):
    bang_namespace = "inputtext"

    def read_measure(self) -> None:
        if not hasattr(self, "_queue"):
            self._queue: list[int] = []

    def update_value(self) -> None:
        self._raw_number = 0.0
        if self.string is None:
            self.string = ""

    def _opt(self, overrides: dict[str, str], key: str, default: str = "") -> str:
        if key in overrides:
            return self.skin.resolve(overrides[key])
        return self.get(key, default)

    def command(self, args: str) -> None:
        word, _sep, rest = args.strip().partition(" ")
        if word.lower() not in ("executebatch", "execute"):
            super().command(args)
            return
        rest = rest.strip().lower()
        indices: list[int] = []
        if rest == "all":
            i = 1
            while self.has(f"command{i}"):
                indices.append(i)
                i += 1
        elif "-" in rest:
            a, _s, b = rest.partition("-")
            indices = list(range(int(a or 1), int(b or a or 1) + 1))
        elif rest.isdigit():
            indices = [int(rest)]
        self._queue = [i for i in indices if self.has(f"command{i}")]
        self._next()

    def _next(self) -> None:
        if not self._queue:
            return
        n = self._queue.pop(0)
        action, ov = split_command(self.get(f"command{n}"))

        def num(key: str, default: float) -> float:
            from raintux.core.formula import parse_number

            return parse_number(self._opt(ov, key, ""), default, self.skin.measure_number)

        style = {
            "background": parse_color(self._opt(ov, "solidcolor", "255,255,255,255"), (1, 1, 1, 1)),
            "color": parse_color(self._opt(ov, "fontcolor", "0,0,0,255"), (0, 0, 0, 1)),
            "face": self._opt(ov, "fontface", "Arial"),
            "size": num("fontsize", 10),
            "bold": "bold" in self._opt(ov, "stringstyle", "normal").lower(),
            "italic": "italic" in self._opt(ov, "stringstyle", "normal").lower(),
            "align": self._opt(ov, "stringalign", "left").lower(),
            "limit": int(num("inputlimit", 0)),
            "number": self._opt(ov, "inputnumber", "0") == "1",
            "focus_dismiss": self._opt(ov, "focusdismiss", "1") != "0",
        }
        rect = (num("x", 0), num("y", 0), max(10.0, num("w", 300)), max(10.0, num("h", 20)))
        default = self._opt(ov, "defaultvalue", "")
        password = self._opt(ov, "password", "0") == "1"

        def answered(text: str | None) -> None:
            if text is None:
                self._queue = []
                self.skin.execute(self.get("ondismissaction"), self)
                return
            self.string = text
            self.skin.execute(action.replace("$UserInput$", text).replace("$userinput$", text), self)
            self._next()

        ask = getattr(self.skin.host, "ask_text", None)
        if ask is None:
            log.warning("[%s] InputText needs a desktop host", self.name)
            return
        ask(self.skin, rect, style, default, password, answered)


def entry_css(style: dict[str, Any]) -> str:
    def rgba(c: tuple[float, float, float, float]) -> str:
        return f"rgba({int(c[0] * 255)},{int(c[1] * 255)},{int(c[2] * 255)},{c[3]:.3f})"

    family = str(style.get("face", "Sans")).replace('"', "")
    return (
        "entry.raintux-input, entry.raintux-input text {"
        f" background: {rgba(style['background'])}; color: {rgba(style['color'])};"
        f' font-family: "{family}"; font-size: {float(style.get("size", 10)):.1f}pt;'
        f" font-weight: {700 if style.get('bold') else 400};"
        f" font-style: {'italic' if style.get('italic') else 'normal'};"
        " border: none; border-radius: 0; box-shadow: none; outline: none; min-height: 0; padding: 0 4px; }"
    )
