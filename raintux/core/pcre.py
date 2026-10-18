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


_POSIX = {"alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "upper": "A-Z", "lower": "a-z",
          "space": r"\s", "xdigit": "0-9A-Fa-f", "word": r"\w", "blank": r" \t", "punct": r"!-/:-@\[-`{-~"}


def _fix_classes(p: str) -> str:
    """Inside ``[...]``: expand POSIX classes and escape what Python reads as nested sets.

    PCRE treats ``[`` and doubled ``-&~|`` in a class as literals; Python warns about them
    (and may change their meaning later).
    """
    out: list[str] = []
    i, n = 0, len(p)
    in_class = False
    while i < n:
        c = p[i]
        if c == "\\" and i + 1 < n:
            if p[i + 1] == "h":  # horizontal whitespace
                out.append(" \t" if in_class else "[ \t]")
            else:
                out.append(p[i : i + 2])
            i += 2
            continue
        if not in_class:
            out.append(c)
            i += 1
            if c == "[":
                in_class = True
                if i < n and p[i] == "^":
                    out.append("^")
                    i += 1
                if i < n and p[i] == "]":
                    out.append("\\]")
                    i += 1
            continue
        if c == "]":
            in_class = False
            out.append(c)
        elif c == "[":
            m = re.match(r"\[:(\^?)([a-z]+):\]", p[i:])
            if m and m.group(2) in _POSIX and not m.group(1):
                out.append(_POSIX[m.group(2)])
                i += m.end()
                continue
            out.append("\\[")
        elif c in "-&~|" and i + 1 < n and p[i + 1] == c:
            out.append("\\" + c + "\\" + c)
            i += 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


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


def _group_end(p: str, start: int) -> int:
    """Index just after the group opening at ``p[start] == "("`` (escapes and classes skipped)."""
    depth, i, in_class = 0, start, False
    while i < len(p):
        c = p[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
            if p[i + 1:i + 2] == "^":
                i += 1
            if p[i + 1:i + 2] == "]":
                i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise re.error("missing ), unterminated subpattern", p, start)


def _top_level_bar(p: str) -> int:
    """Index of the first ``|`` outside groups and classes, or -1."""
    i, in_class, depth = 0, False, 0
    while i < len(p):
        c = p[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            return i
        i += 1
    return -1


def _non_capturing(p: str) -> str:
    return re.sub(r"(?<!\\)\((?!\?)|\(\?P?<(?![=!])[A-Za-z_]\w*>", "(?:", p)


def _conditionals(p: str) -> str:
    """PCRE's ``(?(?=X)yes|no)`` (a condition that is an assertion; Python only takes group
    numbers there) as ``(?:(?=X)yes|(?!X)no)``. Rainmeter skins use it to match an item only if
    there is one more: ``(?(?=.*"name":).*"name": "(.*)")``, repeated."""
    out: list[str] = []
    i = 0
    while (j := p.find("(?(?", i)) >= 0:
        if (len(p[:j]) - len(p[:j].rstrip("\\"))) % 2:  # an escaped "("
            out.append(p[i:j + 1])
            i = j + 1
            continue
        cond_end = _group_end(p, j + 2)
        cond = p[j + 2:cond_end]  # "(?=X)"
        m = re.match(r"\(\?(<?)([=!])", cond)
        if not m:
            out.append(p[i:j + 1])
            i = j + 1
            continue
        end = _group_end(p, j)
        body = p[cond_end:end - 1]
        bar = _top_level_bar(body)
        yes, no = (body, "") if bar < 0 else (body[:bar], body[bar + 1:])
        negated = "(?" + m.group(1) + ("!" if m.group(2) == "=" else "=") + _non_capturing(cond[m.end():])
        out.append(p[i:j] + "(?:" + cond + _conditionals(yes) + "|" + negated + _conditionals(no) + ")")
        i = end
    out.append(p[i:])
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
    p = _conditionals(p)
    p = _fix_classes(p)
    if ungreedy:
        p = _swap_greediness(p)
    return re.compile(p, flags)


def to_python(pattern: str) -> str:
    """Pattern text only (flags inlined) for callers that need a string."""
    rx = compile_pcre(pattern)
    flags = "".join(ch for ch, f in (("i", re.I), ("s", re.S), ("m", re.M), ("x", re.X)) if rx.flags & f)
    return (f"(?{flags})" if flags else "") + rx.pattern
