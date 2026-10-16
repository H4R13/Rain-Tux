"""Parse Rainmeter-style ``.ini`` skins (Phase 1: variables, includes, basic layout)."""

from __future__ import annotations

import configparser
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from raintux.compat.ini_normalizer import read_ini_normalized

log = logging.getLogger(__name__)

_VAR_PATTERN = re.compile(r"#([^#]+)#")


def _casefold_map(d: dict[str, str]) -> dict[str, str]:
    """Map lower-cased keys to values for case-insensitive Rainmeter lookups."""
    return {k.strip().lower(): v.strip() for k, v in d.items()}


def _is_include_key(key: str) -> bool:
    k = key.strip().lower()
    return k == "@include" or k.startswith("@include")


@dataclass
class ParsedSkin:
    """Structured representation of a skin ``.ini`` tree."""

    root: Path
    ini_path: Path
    rainmeter: dict[str, str] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    measures: "OrderedDict[str, dict[str, str]]" = field(default_factory=OrderedDict)
    meters: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def variable_text(self, text: str) -> str:
        """Replace ``#Variables#`` using this skin's definitions (recursive)."""
        return substitute_variables(text, self.variables, self.root)


def substitute_variables(text: str, variables: dict[str, str], root: Path) -> str:
    """Expand ``#NAME#`` placeholders up to a fixed recursion depth."""
    if not text or "#" not in text:
        return text
    # Built-ins common in Rainmeter skins
    builtins = {
        "CURRENTPATH": str(root) + "/",
        "SKINSPATH": str(root.parent) + "/",
    }
    merged = {**{k.upper(): v for k, v in variables.items()}, **{k.upper(): v for k, v in builtins.items()}}

    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return merged.get(key.upper(), match.group(0))

    out = text
    for _ in range(16):
        next_out = _VAR_PATTERN.sub(repl, out)
        if next_out == out:
            break
        out = next_out
    return out


def _merge_ini_into_parser(parser: configparser.ConfigParser, path: Path, stack: set[Path]) -> None:
    """Load normalized INI into parser, processing ``@include`` recursively."""
    path = path.resolve()
    if path in stack:
        log.warning("include cycle skipped: %s", path)
        return
    stack.add(path)
    text = read_ini_normalized(path)
    parser.read_string(text, source=str(path))
    # Collect include directives from all sections
    include_paths: list[Path] = []
    for section in list(parser.sections()):
        items = list(parser.items(section))
        for key, value in items:
            if not _is_include_key(key):
                continue
            variables = dict(parser.items("Variables", raw=True)) if parser.has_section("Variables") else {}
            expanded = substitute_variables(value.strip(), variables, path.parent)
            inc = (path.parent / expanded).resolve()
            if inc.is_file():
                include_paths.append(inc)
            else:
                log.warning("missing include %s (from %s)", inc, path)
            parser.remove_option(section, key)
    for inc in include_paths:
        # Re-read includes as new strings merged — simpler: parse include file and merge sections
        sub = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",), inline_comment_prefixes=(";",), strict=False)
        _merge_ini_into_parser(sub, inc, stack)
        for sec in sub.sections():
            if not parser.has_section(sec):
                parser.add_section(sec)
            for k, v in sub.items(sec, raw=True):
                parser.set(sec, k, v)
    stack.remove(path)


def parse_skin_ini(ini_path: Path) -> ParsedSkin:
    """
    Parse a skin INI with ``[Rainmeter]``, ``[Variables]``, measures, and meters.

    Phase 1: relative positioning tokens ``R`` / ``B`` are interpreted by the
    renderer; formulas support simple conditionals ``(x = y ? a : b)`` in
    :mod:`raintux.measures.calc_measure` (stub) — numeric measures substitute
    ``[MeasureName]`` in strings during render.
    """
    ini_path = ini_path.resolve()
    root = ini_path.parent
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",), inline_comment_prefixes=(";",), strict=False)
    _merge_ini_into_parser(parser, ini_path, set())

    rainmeter: dict[str, str] = {}
    variables: dict[str, str] = {}
    if parser.has_section("Rainmeter"):
        rainmeter = {k: v for k, v in parser.items("Rainmeter", raw=True)}
    if parser.has_section("Variables"):
        variables = {k: v for k, v in parser.items("Variables", raw=True)}

    measures: OrderedDict[str, dict[str, str]] = OrderedDict()
    meters: list[tuple[str, dict[str, str]]] = []

    # Section order preserved
    for section in parser.sections():
        if section in ("Rainmeter", "Variables"):
            continue
        raw = {k: v for k, v in parser.items(section, raw=True)}
        if not raw:
            continue
        cf = _casefold_map(raw)
        if "meter" in cf:
            meters.append((section, raw))
        elif "type" in cf:
            measures[section] = raw
        else:
            # Unknown section — treat as meter if name hints (Rainmeter often [Meter*])
            if section.lower().startswith("meter"):
                meters.append((section, raw))
            else:
                log.debug("ignored section [%s] (no Type/Meter)", section)

    return ParsedSkin(root=root, ini_path=ini_path, rainmeter=rainmeter, variables=variables, measures=measures, meters=meters)
