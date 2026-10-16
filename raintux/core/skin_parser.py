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

_VAR_PATTERN = re.compile(r"#([^#\s]+)#")
_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
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
    """Expand ``#NAME#`` placeholders (case-insensitive) up to a fixed recursion depth.

    ``#*NAME*#`` is an escape that yields a literal ``#NAME#``.
    """
    if not text or "#" not in text:
        return text
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

    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key.startswith("*") and key.endswith("*") and len(key) > 2:
            return "\0" + key[1:-1] + "\0"
        return merged.get(key.upper(), match.group(0))

    out = text
    for _ in range(16):
        next_out = _VAR_PATTERN.sub(repl, out)
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
    def __init__(self, root: Path, resources: Path | None) -> None:
        self.root = root
        self.resources = resources
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

    def read(self, path: Path, stack: tuple[Path, ...], current: str | None = None) -> None:
        """Read ``path`` into the section map; includes are expanded in place."""
        path = path.resolve()
        if path in stack or len(stack) >= _MAX_INCLUDE_DEPTH:
            log.warning("include cycle or depth limit, skipped: %s", path)
            return
        try:
            text = read_ini_normalized(path)
        except OSError as e:
            log.warning("cannot read %s: %s", path, e)
            return
        for line in text.split("\n"):
            s = line.strip()
            if not s or s.startswith(";"):
                continue
            m = _SECTION.match(s)
            if m:
                current = m.group(1).strip()
                self.section(current)
                continue
            if "=" not in s or current is None:
                continue
            key, _sep, value = s.partition("=")
            key = key.strip().lower()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"' and value.count('"') == 2:
                # Rainmeter strips one pair of enclosing quotes.
                value = value[1:-1]
            if _is_include_key(key):
                inc_rel = substitute_variables(value, self.variables(), path.parent, self.resources)
                inc = Path(inc_rel)
                inc = inc if inc.is_absolute() else (path.parent / inc)
                if inc.is_file():
                    self.read(inc, stack + (path,), current)
                else:
                    log.warning("missing include %s (from %s)", inc, path)
                continue
            self.section(current)[key] = value


def parse_skin_ini(ini_path: Path, resources: Path | None = None) -> ParsedSkin:
    """Parse a skin INI with ``[Rainmeter]``, ``[Variables]``, measures, and meters."""
    ini_path = ini_path.resolve()
    root = ini_path.parent
    resources = resources or find_resources(root)
    reader = _Reader(root, resources)
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
