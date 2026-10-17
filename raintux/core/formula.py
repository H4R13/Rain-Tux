"""Rainmeter formula evaluator (``Formula=`` in Calc measures, ``(…)`` option values).

Grammar follows Rainmeter's math parser: ``+ - * / % **``, bitwise ``& | ^ ~ << >>``,
comparisons ``= == != <> < > <= >=``, logic ``&& || !``, ternary ``a ? b : c``,
functions (``abs``, ``clamp``, ``round``, …) and constants ``pi`` / ``e``.
Bare identifiers are resolved through a callback (measure names in Calc formulas).
Division or modulo by zero yields 0, as in Rainmeter. No ``eval``: this is a small
recursive-descent parser, so skins cannot run Python through formulas.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable

Resolver = Callable[[str], "float | None"]


class FormulaError(ValueError):
    """Raised for syntax errors and unknown identifiers."""


_TOKEN = re.compile(
    r"""
    \s*(?:
      (?P<num>0x[0-9a-fA-F]+|0b[01]+|0o[0-7]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<op>\*\*|<<|>>|<=|>=|==|!=|<>|&&|\|\||[-+*/%^&|~!<>=?:(),])
    )""",
    re.VERBOSE,
)


def _tokenize(src: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    src = src.rstrip()
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise FormulaError(f"unexpected character {src[pos:pos + 1]!r} at {pos} in {src!r}")
        pos = m.end()
        kind = m.lastgroup
        assert kind is not None
        out.append((kind, m.group(kind)))
    return out


def _num(text: str) -> float:
    t = text.lower()
    if t.startswith("0x"):
        return float(int(t, 16))
    if t.startswith("0b"):
        return float(int(t[2:], 2))
    if t.startswith("0o"):
        return float(int(t[2:], 8))
    return float(text)


def _round(x: float, precision: float = 0) -> float:
    p = int(precision)
    # Rainmeter rounds half away from zero.
    f = 10.0**p
    return math.floor(abs(x) * f + 0.5) / f * (1 if x >= 0 else -1)


def _safe(fn: Callable[..., float]) -> Callable[..., float]:
    def wrapped(*a: float) -> float:
        try:
            r = fn(*a)
        except (ValueError, OverflowError, ZeroDivisionError):
            return 0.0
        return 0.0 if (isinstance(r, float) and (math.isnan(r) or math.isinf(r))) else float(r)

    return wrapped


_FUNCS: dict[str, tuple[int, int, Callable[..., float]]] = {
    # name: (min args, max args, fn)
    "abs": (1, 1, abs),
    "acos": (1, 1, _safe(math.acos)),
    "asin": (1, 1, _safe(math.asin)),
    "atan": (1, 1, _safe(math.atan)),
    "atan2": (2, 2, _safe(math.atan2)),
    "ceil": (1, 1, lambda x: float(math.ceil(x))),
    "clamp": (3, 3, lambda x, lo, hi: max(lo, min(hi, x))),
    "cos": (1, 1, math.cos),
    "exp": (1, 1, _safe(math.exp)),
    "floor": (1, 1, lambda x: float(math.floor(x))),
    "frac": (1, 1, lambda x: x - math.trunc(x)),
    "ln": (1, 1, _safe(math.log)),
    "log": (1, 1, _safe(math.log10)),
    "max": (2, 2, max),
    "min": (2, 2, min),
    "rad": (1, 1, math.radians),
    "deg": (1, 1, math.degrees),
    "round": (1, 2, _round),
    "sgn": (1, 1, lambda x: float((x > 0) - (x < 0))),
    "sin": (1, 1, math.sin),
    "sqrt": (1, 1, _safe(math.sqrt)),
    "tan": (1, 1, _safe(math.tan)),
    "trunc": (1, 1, lambda x: float(math.trunc(x))),
}
_CONSTS = {"pi": math.pi, "e": math.e}

# Binary operator precedence (higher binds tighter); ``**`` is right-associative.
_BINARY: dict[str, int] = {
    "||": 1,
    "&&": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "=": 6, "==": 6, "!=": 6, "<>": 6,
    "<": 7, ">": 7, "<=": 7, ">=": 7,
    "<<": 8, ">>": 8,
    "+": 9, "-": 9,
    "*": 10, "/": 10, "%": 10,
    "**": 12,
}
_UNARY_PREC = 11


def _apply(op: str, a: float, b: float) -> float:
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return a / b if b else 0.0
    if op == "%":
        return math.fmod(a, b) if b else 0.0
    if op == "**":
        try:
            r = a**b
        except (OverflowError, ZeroDivisionError):
            return 0.0
        return 0.0 if isinstance(r, complex) else float(r)
    if op in ("=", "=="):
        return float(a == b)
    if op in ("!=", "<>"):
        return float(a != b)
    if op == "<":
        return float(a < b)
    if op == ">":
        return float(a > b)
    if op == "<=":
        return float(a <= b)
    if op == ">=":
        return float(a >= b)
    if op == "&&":
        return float(bool(a) and bool(b))
    if op == "||":
        return float(bool(a) or bool(b))
    ia, ib = int(a), int(b)
    if op == "&":
        return float(ia & ib)
    if op == "|":
        return float(ia | ib)
    if op == "^":
        return float(ia ^ ib)
    if op == "<<":
        return float(ia << ib) if 0 <= ib < 64 else 0.0
    if op == ">>":
        return float(ia >> ib) if 0 <= ib < 64 else 0.0
    raise FormulaError(f"unknown operator {op}")


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], resolve: Resolver | None) -> None:
        self.t = tokens
        self.i = 0
        self.resolve = resolve

    def peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise FormulaError("unexpected end of formula")
        if value is not None and tok[1] != value:
            raise FormulaError(f"expected {value!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def expr(self) -> float:
        cond = self.binary(0)
        tok = self.peek()
        if tok and tok[1] == "?":
            self.take("?")
            a = self.expr()
            self.take(":")
            b = self.expr()
            return a if cond else b
        return cond

    def binary(self, min_prec: int) -> float:
        left = self.unary()
        while True:
            tok = self.peek()
            if not tok or tok[0] != "op" or tok[1] not in _BINARY or _BINARY[tok[1]] < min_prec:
                return left
            op = tok[1]
            self.take()
            prec = _BINARY[op]
            right = self.binary(prec if op == "**" else prec + 1)
            left = _apply(op, left, right)

    def unary(self) -> float:
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in ("-", "+", "!", "~"):
            self.take()
            v = self.binary(_UNARY_PREC)
            if tok[1] == "-":
                return -v
            if tok[1] == "!":
                return float(not v)
            if tok[1] == "~":
                return float(~int(v))
            return v
        return self.primary()

    def primary(self) -> float:
        kind, text = self.take()
        if kind == "num":
            return _num(text)
        if kind == "op" and text == "(":
            v = self.expr()
            self.take(")")
            return v
        if kind == "name":
            low = text.lower()
            nxt = self.peek()
            if low in _FUNCS and nxt and nxt[1] == "(":
                self.take("(")
                args: list[float] = []
                if self.peek() and self.peek()[1] != ")":  # type: ignore[index]
                    args.append(self.expr())
                    while self.peek() and self.peek()[1] == ",":  # type: ignore[index]
                        self.take(",")
                        args.append(self.expr())
                self.take(")")
                lo, hi, fn = _FUNCS[low]
                if not lo <= len(args) <= hi:
                    raise FormulaError(f"{low}() takes {lo}-{hi} arguments")
                return float(fn(*args))
            if low in _CONSTS:
                return _CONSTS[low]
            if self.resolve is not None:
                v = self.resolve(text)
                if v is not None:
                    return float(v)
            raise FormulaError(f"unknown identifier {text!r}")
        raise FormulaError(f"unexpected {text!r}")


def evaluate(src: str, resolve: Resolver | None = None) -> float:
    """Evaluate ``src``; raise :class:`FormulaError` when it does not parse."""
    tokens = _tokenize(src)
    if not tokens:
        raise FormulaError("empty formula")
    p = _Parser(tokens, resolve)
    v = p.expr()
    if p.peek() is not None:
        raise FormulaError(f"trailing input {p.peek()[1]!r} in {src!r}")  # type: ignore[index]
    return v


def parse_number(value: str, default: float = 0.0, resolve: Resolver | None = None) -> float:
    """Numeric option value: a plain number, or a formula when it starts with ``(``.

    Like Rainmeter's ParseUtil, the whole text is the formula, so ``((a+b)*c)/2`` works;
    a formula error gives ``default``.
    """
    v = value.strip()
    if not v:
        return default
    if v.startswith("("):
        try:
            return evaluate(v, resolve)
        except (FormulaError, ValueError, TypeError):
            return default
    try:
        return _num(v)
    except (FormulaError, ValueError, TypeError):
        # Rainmeter reads the leading number of things like "12px" or "5.5 ".
        m = re.match(r"\s*[-+]?(\d+\.?\d*|\.\d+)", v)
        return float(m.group(0)) if m else default


def random_between(lo: float, hi: float, integer: bool = True) -> float:
    if hi < lo:
        lo, hi = hi, lo
    return float(random.randint(int(lo), int(hi))) if integer else random.uniform(lo, hi)
