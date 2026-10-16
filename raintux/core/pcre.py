"""Translate the PCRE dialect used by Rainmeter skins into Python ``re`` patterns.

Handles leading inline flags including PCRE's ``U`` (ungreedy: every quantifier's
greediness is swapped), ``(?<name>…)`` named groups and ``\\h``. Python 3.11+
already understands possessive quantifiers and atomic groups.
"""

from __future__ import annotations

import functools
import re

_FLAG_GROUP = re.compile(r"^\(\?([a-zA-Z]+)\)")
_FLAGS = {"i": re.IGNORECASE, "s": re.DOTALL, "m": re.MULTILINE, "x": re.VERBOSE}


def _swap_greediness(p: str) -> str:
    out: list[str] = []
    i, n = 0, len(p)
    in_class = False
    while i < n:
        c = p[i]
        if c == "\\" and i + 1 < n:
            out.append(p[i : i + 2])
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
            out.append(c)
            i += 1
            continue
        if c == "[":
            in_class = True
            out.append(c)
            i += 1
            if i < n and p[i] == "^":
                out.append("^")
                i += 1
            if i < n and p[i] == "]":
                out.append("]")
                i += 1
            continue
        quant_end = -1
        if c in "*+?" and not (c == "?" and out and out[-1] == "("):
            quant_end = i + 1
        elif c == "{":
            m = re.match(r"\{\d+(,\d*)?\}", p[i:])
            if m:
                quant_end = i + m.end()
        if quant_end < 0:
            out.append(c)
            i += 1
            continue
        out.append(p[i:quant_end])
        i = quant_end
        if i < n and p[i] == "?":
            i += 1  # lazy in ungreedy mode -> greedy
        elif i < n and p[i] == "+":
            out.append("+")  # possessive stays possessive
            i += 1
        else:
            out.append("?")
    return "".join(out)


@functools.lru_cache(maxsize=512)
def compile_pcre(pattern: str) -> re.Pattern[str]:
    """Compile a Rainmeter (PCRE) regular expression."""
    flags = 0
    ungreedy = False
    p = pattern
    m = _FLAG_GROUP.match(p)
    if m:
        letters = m.group(1)
        if set(letters.lower()) <= set("isxmu"):
            p = p[m.end():]
            for ch in letters:
                if ch == "U":
                    ungreedy = True
                elif ch.lower() in _FLAGS:
                    flags |= _FLAGS[ch.lower()]
    p = re.sub(r"\(\?<(?=[A-Za-z_])", "(?P<", p)
    p = p.replace("\\h", "[ \\t]")
    if ungreedy:
        p = _swap_greediness(p)
    return re.compile(p, flags)


def to_python(pattern: str) -> str:
    """Pattern text only (flags inlined) for callers that need a string."""
    rx = compile_pcre(pattern)
    flags = "".join(ch for ch, f in (("i", re.I), ("s", re.S), ("m", re.M), ("x", re.X)) if rx.flags & f)
    return (f"(?{flags})" if flags else "") + rx.pattern
