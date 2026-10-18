"""Read Rainmeter-style ``.ini`` skins: sections, ``@Include``, variables.

This is a small hand-written reader instead of :mod:`configparser` because
Rainmeter's format is looser: stray lines without ``=`` are ignored, keys are
case-insensitive, values may contain ``%``/``;`` freely, and ``@Include`` pulls
another file's sections in *at that point* (later keys override earlier ones).
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from raintux.compat.ini_normalizer import read_ini_normalized

log = logging.getLogger(__name__)

_NEW_STYLE_VAR = re.compile(r"\[#([^\[\]#]+)\]")
_MAX_INCLUDE_DEPTH = 16


def _is_include_key(key: str) -> bool:
    return key.strip().lower().startswith("@include")


@dataclass
class ParsedSkin:
    """Structured representation of a skin ``.ini`` tree (keys lower-cased)."""

    root: Path
    ini_path: Path
    rainmeter: dict[str, str] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    measures: "OrderedDict[str, dict[str, str]]" = field(default_factory=OrderedDict)
    meters: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    # Every section in file order (including [Rainmeter], [Variables], styles).
    sections: "OrderedDict[str, OrderedDict[str, str]]" = field(default_factory=OrderedDict)
    resources: Path | None = None

    def variable_text(self, text: str) -> str:
        """Replace ``#Variables#`` using this skin's definitions (recursive)."""
        return substitute_variables(text, self.variables, self.root, self.resources)


def substitute_variables(
    text: str,
    variables: dict[str, str],
    root: Path,
    resources: Path | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    """Expand ``#NAME#`` placeholders (case-insensitive); see :func:`expand_variables`."""
    if not text or "#" not in text:
        return text
    return expand_variables(text, variable_table(variables, root, resources, extra))


def variable_table(
    variables: dict[str, str],
    root: Path,
    resources: Path | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Upper-cased name -> value for :func:`expand_variables`."""
    # Built-ins win over [Variables] (Rainmeter reserves them); ``extra`` carries the
    # skin's full set of built-ins and overrides the defaults here.
    merged = {k.upper(): v for k, v in variables.items()}
    merged.update(
        {
            "CURRENTPATH": str(root) + "/",
            "SKINSPATH": str(root.parent) + "/",
            "@": str(resources or root / "@Resources") + "/",
            "CRLF": "\n",
        }
    )
    if extra:
        merged.update({k.upper(): v for k, v in extra.items()})
    return merged


def _expand_once(text: str, table: dict[str, str]) -> str:
    """One pass of Rainmeter's ``ConfigParser::ReplaceVariables``: the text between a ``#`` and the
    next one is a name (spaces allowed, ``#Recycle Bin#``); when it is no variable, its closing ``#``
    may open the next one."""
    out: list[str] = []
    i = start = 0
    while (start := text.find("#", start)) >= 0:
        end = text.find("#", start + 1)
        if end < 0:
            break
        name = text[start + 1:end]
        if len(name) >= 2 and name[0] == "*" and name[-1] == "*":
            out.append(text[i:start] + "\0" + name[1:-1] + "\0")  # #*NAME*# -> a literal #NAME#
            i = start = end + 1
            continue
        value = table.get(name.upper())
        if value is None:
            start = end
            continue
        out.append(text[i:start] + value)
        i = start = end + 1
    out.append(text[i:])
    return "".join(out)


def expand_variables(text: str, table: dict[str, str]) -> str:
    """Expand ``#NAME#`` placeholders from ``table`` up to a fixed recursion depth.

    ``#*NAME*#`` is an escape that yields a literal ``#NAME#``.
    """
    if not text or "#" not in text:
        return text
    out = text
    for _ in range(16):
        next_out = _expand_once(out, table)
        if next_out == out:
            break
        out = next_out
    return out.replace("\0", "#")


def find_resources(root: Path, skins_root: Path | None = None) -> Path:
    """``@Resources`` of the root config (first folder below the skins root)."""
    if skins_root is not None:
        try:
            rel = root.resolve().relative_to(skins_root.resolve())
            if rel.parts:
                return skins_root.resolve() / rel.parts[0] / "@Resources"
        except ValueError:
            pass
    # Without a known skins root, walk up looking for an existing @Resources.
    for p in (root, *root.parents):
        if (p / "@Resources").is_dir():
            return p / "@Resources"
    return root / "@Resources"


class _Reader:
    def __init__(self, root: Path, resources: Path | None, builtins: dict[str, str] | None = None) -> None:
        self.root = root
        self.resources = resources
        self.builtins = builtins  # e.g. #ROOTCONFIGPATH# / #SETTINGSPATH# in @Include paths
        self.sections: OrderedDict[str, OrderedDict[str, str]] = OrderedDict()
        # Case-insensitive section lookup: lower name -> canonical name.
        self.names: dict[str, str] = {}

    def section(self, name: str) -> OrderedDict[str, str]:
        key = name.strip().lower()
        if key not in self.names:
            self.names[key] = name.strip()
            self.sections[name.strip()] = OrderedDict()
        return self.sections[self.names[key]]

    def variables(self) -> dict[str, str]:
        canon = self.names.get("variables")
        return dict(self.sections[canon]) if canon else {}

    def read(self, path: Path, stack: tuple[Path, ...]) -> None:
        """Read ``path`` into the section map; includes are expanded in place.

        As Rainmeter's ConfigParser::ReadIniFile: within one file only the first of duplicate
        sections and the first of duplicate keys in a section count, while a later file (an
        @Include) overrides earlier values; a line starting with "[" opens a section named up to
        the first "]"; keys before the first section are ignored.
        """
        path = path.resolve()
        if path in stack or len(stack) >= _MAX_INCLUDE_DEPTH:
            log.warning("include cycle or depth limit, skipped: %s", path)
            return
        try:
            text = read_ini_normalized(path)
        except OSError as e:
            log.warning("cannot read %s: %s", path, e)
            return
        current: str | None = None
        file_sections: set[str] = set()
        section_keys: set[str] = set()
        for line in text.split("\n"):
            s = line.strip()
            if not s or s.startswith(";"):
                continue
            if s.startswith("["):
                name = s[1:].split("]", 1)[0].strip()
                section_keys = set()
                current = None if name.lower() in file_sections else name  # a repeated section is not read
                file_sections.add(name.lower())
                if current is not None:
                    self.section(current)
                continue
            if "=" not in s or current is None:
                continue
            key, _sep, value = s.partition("=")
            key = key.strip().lower()
            if not key or key in section_keys:
                continue
            section_keys.add(key)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"' and value.count('"') == 2:
                # Rainmeter strips one pair of enclosing quotes.
                value = value[1:-1]
            if _is_include_key(key):
                if value:
                    self._include(value, path, stack)
                continue
            self.section(current)[key] = value

    def _include(self, value: str, path: Path, stack: tuple[Path, ...]) -> None:
        from raintux.core.paths import resolve_ci, windows_to_posix

        table = variable_table(self.variables(), path.parent, self.resources, self.builtins)
        # Rainmeter expands new-style [#Var] here too (Enigma: @Include=[#@]Styles\[#Stylesheet]\...).
        text = _NEW_STYLE_VAR.sub(lambda m: table.get(m.group(1).upper(), m.group(0)), value)
        inc = Path(windows_to_posix(expand_variables(text, table)))
        inc = resolve_ci(inc if inc.is_absolute() else (path.parent / inc))
        if inc.is_file():
            self.read(inc, stack + (path,))
        else:
            log.warning("missing include %s (from %s)", inc, path)


def parse_skin_ini(ini_path: Path, resources: Path | None = None, builtins: dict[str, str] | None = None) -> ParsedSkin:
    """Parse a skin INI with ``[Rainmeter]``, ``[Variables]``, measures, and meters.

    ``builtins`` are the skin's built-in variables, used in ``@Include`` paths.
    """
    ini_path = ini_path.resolve()
    root = ini_path.parent
    resources = resources or find_resources(root)
    reader = _Reader(root, resources, builtins)
    reader.read(ini_path, ())

    sections = reader.sections

    def get(name: str) -> dict[str, str]:
        return dict(sections.get(reader.names.get(name, ""), {}))

    measures: OrderedDict[str, dict[str, str]] = OrderedDict()
    meters: list[tuple[str, dict[str, str]]] = []
    for name, raw in sections.items():
        if name.lower() in ("rainmeter", "variables", "metadata"):
            continue
        if "meter" in raw:
            meters.append((name, dict(raw)))
        elif "measure" in raw or "type" in raw:
            measures[name] = dict(raw)
    return ParsedSkin(
        root=root,
        ini_path=ini_path,
        rainmeter=get("rainmeter"),
        variables=get("variables"),
        measures=measures,
        meters=meters,
        sections=sections,
        resources=resources,
    )
