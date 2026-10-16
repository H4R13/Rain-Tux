"""One loaded skin: variables, measures, meters, the update cycle, bangs and mouse input.

This module has no GTK dependency. Everything that touches the desktop (windows,
timers, running programs, other skins) goes through a :class:`SkinHost`, so the
whole Rainmeter behaviour can be unit-tested with :class:`NullHost`.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from raintux.core.bangs import split_args, split_commands
from raintux.core.formula import FormulaError, evaluate, parse_number
from raintux.core.section import Section
from raintux.core.ini_writer import write_key_value
from raintux.core.skin_parser import ParsedSkin, find_resources, parse_skin_ini, substitute_variables
from raintux.measures import create_measure
from raintux.measures.base_measure import Measure, format_number, trim_number

if TYPE_CHECKING:
    from raintux.meters.base_meter import Meter

log = logging.getLogger(__name__)

_SECTION_VAR = re.compile(r"\[([^\[\]]+)\]")
_CHAR_REF = re.compile(r"\[\\(x[0-9a-fA-F]{1,6}|\d{1,7})\]")
_MAX_ACTION_DEPTH = 32

# Bangs that act on the window / the app rather than on this skin's sections.
HOST_BANGS = {
    "refresh", "refreshapp", "refreshgroup", "move", "setwindowposition", "setanchor", "draggable",
    "keeponscreen", "clickthrough", "snapedges", "zpos", "settransparency", "fadeduration",
    "show", "hide", "toggle", "showfade", "hidefade", "togglefade", "showgroup", "hidegroup",
    "togglegroup", "activateconfig", "deactivateconfig", "toggleconfig", "deactivateconfiggroup",
    "manage", "about", "skinmenu", "traymenu", "quit", "edit", "editskin", "setwallpaper",
    "loadlayout", "showblur", "hideblur", "toggleblur", "addblur", "removeblur", "zposgroup",
    "draggablegroup", "clickthroughgroup", "keeponscreengroup", "snapedgesgroup",
    "settransparencygroup", "fadedurationgroup", "setclip", "resetstats", "lsboxhook",
}

# Bangs whose optional last argument is a parameter rather than a config name.
_OPTIONAL_EXTRA = {"writekeyvalue", "log", "execute"}

# Number of required arguments for skin-local bangs; one extra argument is a config name.
_LOCAL_ARGC: dict[str, int] = {
    "setvariable": 2, "setoption": 3, "setoptiongroup": 3, "setvariablegroup": 3,
    "writekeyvalue": 3, "update": 0, "redraw": 0, "updatemeasure": 1, "updatemeter": 1,
    "updatemeasuregroup": 1, "updatemetergroup": 1, "showmeter": 1, "hidemeter": 1,
    "togglemeter": 1, "showmetergroup": 1, "hidemetergroup": 1, "togglemetergroup": 1,
    "enablemeasure": 1, "disablemeasure": 1, "togglemeasure": 1, "enablemeasuregroup": 1,
    "disablemeasuregroup": 1, "togglemeasuregroup": 1, "pausemeasure": 1, "unpausemeasure": 1,
    "togglepausemeasure": 1, "pausemeasuregroup": 1, "unpausemeasuregroup": 1,
    "togglepausemeasuregroup": 1, "commandmeasure": 2, "pluginbang": 1, "movemeter": 3,
    "log": 1, "execute": 1, "delay": 1,
}


class SkinHost(Protocol):
    """What a skin needs from the desktop side (implemented by the GTK engine)."""

    def redraw(self, skin: Skin) -> None: ...
    def resize(self, skin: Skin, width: int, height: int) -> None: ...
    def call_later(self, ms: int, fn: Callable[[], None]) -> Any: ...
    def cancel(self, handle: Any) -> None: ...
    def call_soon(self, fn: Callable[[], None]) -> None: ...
    def run_command(self, skin: Skin, command: str) -> None: ...
    def host_bang(self, skin: Skin, name: str, args: list[str]) -> None: ...
    def find_skin(self, config: str) -> Skin | None: ...
    def screen_variables(self, skin: Skin) -> dict[str, str]: ...
    def set_pointer(self, skin: Skin, cursor: str | None, tooltip: str | None) -> None: ...


class NullHost:
    """Headless host for tests and tools: records commands, runs callbacks immediately."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.host_bangs: list[tuple[str, list[str]]] = []
        self.timers: list[tuple[int, Callable[[], None]]] = []
        self.size: tuple[int, int] = (0, 0)
        self.redraws = 0
        self.skins: dict[str, Skin] = {}
        self.pointer: tuple[str | None, str | None] = (None, None)

    def redraw(self, skin: Skin) -> None:
        self.redraws += 1

    def resize(self, skin: Skin, width: int, height: int) -> None:
        self.size = (width, height)

    def call_later(self, ms: int, fn: Callable[[], None]) -> Any:
        self.timers.append((ms, fn))
        return len(self.timers) - 1

    def cancel(self, handle: Any) -> None:
        if isinstance(handle, int) and 0 <= handle < len(self.timers):
            self.timers[handle] = (self.timers[handle][0], lambda: None)

    def run_timers(self) -> None:
        """Run queued call_later callbacks (including ones they schedule)."""
        while self.timers:
            _ms, fn = self.timers.pop(0)
            fn()

    def call_soon(self, fn: Callable[[], None]) -> None:
        fn()

    def run_command(self, skin: Skin, command: str) -> None:
        self.commands.append(" ".join(split_args(command)))

    def host_bang(self, skin: Skin, name: str, args: list[str]) -> None:
        self.host_bangs.append((name, args))

    def find_skin(self, config: str) -> Skin | None:
        return self.skins.get(config.replace("/", "\\").lower())

    def screen_variables(self, skin: Skin) -> dict[str, str]:
        return {"SCREENAREAWIDTH": "1920", "SCREENAREAHEIGHT": "1080", "WORKAREAWIDTH": "1920", "WORKAREAHEIGHT": "1080"}

    def set_pointer(self, skin: Skin, cursor: str | None, tooltip: str | None) -> None:
        self.pointer = (cursor, tooltip)


@dataclass
class WindowState:
    """Per-config window settings (Rainmeter's Rainmeter.ini keys), persisted by the engine."""

    x: int = 0
    y: int = 0
    z: int = 0  # AlwaysOnTop: -2 desktop, -1 bottom, 0 normal, 1 topmost, 2 stay topmost
    draggable: bool = True
    click_through: bool = False
    keep_on_screen: bool = True
    save_position: bool = True
    snap_edges: bool = True
    alpha: int = 255
    fade_duration: int = 250
    hide_on_mouse_over: bool = False
    anchor_x: str = "0"
    anchor_y: str = "0"


class RainmeterSection(Section):
    """The ``[Rainmeter]`` section (skin-wide options and actions)."""

    kind = "rainmeter"

    def read_options(self) -> None:
        self.skin.update_ms = int(self.get_number("update", 1000))
        self.skin.default_update_divider = self.get_int("defaultupdatedivider", 1) or 1
        self.skin.dynamic_window_size = self.get_bool("dynamicwindowsize")
        self.skin.accurate_text = self.get_bool("accuratetext")


class Skin:
    """Runtime state of one ``.ini`` file of one config."""

    def __init__(
        self,
        ini_path: Path,
        config: str | None = None,
        host: SkinHost | None = None,
        skins_root: Path | None = None,
        state: WindowState | None = None,
    ) -> None:
        self.ini_path = Path(ini_path).resolve()
        self.root = self.ini_path.parent
        self.skins_root = skins_root.resolve() if skins_root else self.root.parent
        try:
            rel = self.root.relative_to(self.skins_root)
            default_config = "\\".join(rel.parts) or self.root.name
        except ValueError:
            default_config = self.root.name
        self.config = (config or default_config).replace("/", "\\")
        self.host: SkinHost = host or NullHost()
        self.state = state or WindowState()
        self.resources = find_resources(self.root, self.skins_root)
        self.parsed: ParsedSkin | None = None
        self.variables: OrderedDict[str, str] = OrderedDict()
        self.rm: RainmeterSection = RainmeterSection(self, "Rainmeter", {})
        self.measures: OrderedDict[str, Measure] = OrderedDict()
        self.meters: list[Meter] = []
        self.meter_map: dict[str, Meter] = {}
        self.styles: dict[str, dict[str, str]] = {}
        self.update_ms = 1000
        self.default_update_divider = 1
        self.dynamic_window_size = False
        self.accurate_text = False
        self.counter = 0
        self.width = 0
        self.height = 0
        self.loaded = False
        self.closed = False
        self._depth = 0
        self._hover: set[str] = set()
        self._mouse_over_skin = False
        self._pressed_button: Meter | None = None
        self._builtins_cache: dict[str, str] | None = None
        self.on_size_changed: Callable[[int, int], None] | None = None

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        """Parse the INI and create sections (no update yet)."""
        from raintux.meters import create_meter

        self.parsed = parse_skin_ini(self.ini_path, self.resources)
        self.variables = OrderedDict((k.lower(), v) for k, v in self.parsed.variables.items())
        self.styles = {name.lower(): dict(opts) for name, opts in self.parsed.sections.items()}
        self.rm = RainmeterSection(self, "Rainmeter", self.parsed.rainmeter)
        self.rm.read()
        self.measures = OrderedDict()
        for name, raw in self.parsed.measures.items():
            self.measures[name.lower()] = create_measure(self, name, raw)
        self.meters = []
        self.meter_map = {}
        prev: Meter | None = None
        for name, raw in self.parsed.meters:
            meter = create_meter(self, name, raw)
            meter.relative_to = prev
            self.meters.append(meter)
            self.meter_map[name.lower()] = meter
            prev = meter
        for m in self.measures.values():
            m.read()
        for mt in self.meters:
            mt.read()
        self.loaded = True

    def start(self) -> None:
        """First update, then ``OnRefreshAction`` (Rainmeter order)."""
        self.update()
        self.execute(self.rm.get("onrefreshaction"), self.rm)

    def close(self) -> None:
        """Run ``OnCloseAction`` and stop measure threads."""
        if self.closed:
            return
        self.closed = True
        try:
            self.execute(self.rm.get("oncloseaction"), self.rm)
        finally:
            for m in self.measures.values():
                try:
                    m.finalize()
                except Exception:
                    log.exception("finalize [%s]", m.name)

    # ------------------------------------------------------------------ lookup
    def measure(self, name: str) -> Measure | None:
        return self.measures.get(name.strip().lower())

    def meter(self, name: str) -> Meter | None:
        return self.meter_map.get(name.strip().lower())

    def section(self, name: str) -> Section | None:
        n = name.strip().lower()
        if n == "rainmeter":
            return self.rm
        return self.measures.get(n) or self.meter_map.get(n)

    def measure_number(self, name: str) -> float | None:
        m = self.measures.get(name.strip().lower())
        return m.value if m is not None else None

    def style(self, name: str) -> dict[str, str] | None:
        return self.styles.get(name.strip().lower())

    # ------------------------------------------------------------------ variables
    def builtins(self) -> dict[str, str]:
        if self._builtins_cache is None:
            root_config = self.config.split("\\")[0]
            b = {
                "CURRENTFILE": self.ini_path.name,
                "CURRENTCONFIG": self.config,
                "CURRENTPATH": str(self.root) + "/",
                "ROOTCONFIG": root_config,
                "ROOTCONFIGPATH": str(self.skins_root / root_config) + "/",
                "SKINSPATH": str(self.skins_root) + "/",
                "@": str(self.resources) + "/",
                "SETTINGSPATH": str(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "raintux") + "/",
                "PROGRAMPATH": str(Path(__file__).resolve().parent.parent) + "/",
                "ADDONSPATH": str(self.skins_root.parent / "Addons") + "/",
                "PLUGINSPATH": str(self.skins_root.parent / "Plugins") + "/",
                "CONFIGEDITOR": os.environ.get("VISUAL") or os.environ.get("EDITOR") or "xdg-open",
                "CRLF": "\n",
            }
            b.update(self.host.screen_variables(self))
            self._builtins_cache = b
        b = dict(self._builtins_cache)
        b.update(
            {
                "CURRENTCONFIGX": str(self.state.x),
                "CURRENTCONFIGY": str(self.state.y),
                "CURRENTCONFIGWIDTH": str(self.width),
                "CURRENTCONFIGHEIGHT": str(self.height),
            }
        )
        return b

    def invalidate_builtins(self) -> None:
        self._builtins_cache = None

    def get_variable(self, name: str) -> str | None:
        n = name.strip().lower()
        if n in self.variables:
            return self.resolve(self.variables[n])
        b = self.builtins()
        return b.get(name.strip().upper())

    def set_variable(self, name: str, value: str) -> None:
        self.variables[name.strip().lower()] = value

    def resolve(self, text: str, section_vars: bool = False) -> str:
        """Expand ``#Variables#``, character references and (optionally) ``[Section]`` variables."""
        if not text:
            return text
        out = substitute_variables(text, self.variables, self.root, self.resources, self.builtins())
        if "[" in out:
            out = _CHAR_REF.sub(_char_ref, out)
            if section_vars:
                out = self.replace_section_variables(out)
        return out

    def replace_section_variables(self, text: str) -> str:
        placeholders: list[str] = []

        def repl(m: re.Match[str]) -> str:
            inner = m.group(1)
            if len(inner) > 2 and inner.startswith("*") and inner.endswith("*"):
                placeholders.append("[" + inner[1:-1] + "]")
                return f"\0{len(placeholders) - 1}\0"
            value = self.section_variable(inner)
            return m.group(0) if value is None else value

        out = text
        for _ in range(8):
            nxt = _SECTION_VAR.sub(repl, out)
            if nxt == out:
                break
            out = nxt
        if placeholders:
            out = re.sub(r"\0(\d+)\0", lambda m: placeholders[int(m.group(1))], out)
        return out

    def section_variable(self, spec: str) -> str | None:
        """Value for ``[Name]``, ``[Name:]``, ``[Name:%,1]``, ``[Name:/1024,2]``, ``[Meter:W]``, ``[#Var]``…"""
        if spec.startswith("#"):
            return self.get_variable(spec[1:])
        if spec.startswith("&"):
            return self._inline_script(spec[1:])
        if spec.startswith(("!", "\\")):
            return None
        name, colon, param = spec.partition(":")
        name = name.strip()
        m = self.measure(name)
        if m is not None:
            if not colon:
                return m.string_value()
            p = param.strip()
            low = p.lower()
            if low == "maxvalue":
                return trim_number(m.max_value)
            if low == "minvalue":
                return trim_number(m.min_value)
            if low == "escaperegexp":
                return re.escape(m.string_value())
            if low == "encodeurl":
                return urllib.parse.quote(m.string_value(), safe="")
            if low == "timestamp":
                return trim_number(m.value)
            percent = False
            scale = 1.0
            decimals: int | None = None
            for part in filter(None, re.split(r"(?=[%/,])", p)):
                if part == "%":
                    percent = True
                elif part.startswith("/"):
                    scale = parse_number(part[1:], 1.0) or 1.0
                elif part.startswith(","):
                    decimals = int(parse_number(part[1:], 0))
            v = m.relative() * 100 if percent else m.value / scale
            return trim_number(v) if decimals is None else format_number(v, decimals)
        mt = self.meter(name)
        if mt is not None and colon:
            key = param.strip().upper()
            x, y, w, h = mt.box()
            vals = {"X": x, "Y": y, "W": w, "H": h, "XW": x + w, "YH": y + h}
            if key in vals:
                return trim_number(vals[key])
        return None

    def _inline_script(self, spec: str) -> str | None:
        """``[&ScriptMeasure:func('arg')]`` → call into a Lua script measure."""
        name, _, call = spec.partition(":")
        m = self.measure(name)
        if m is None or not hasattr(m, "inline_call"):
            return None
        try:
            return str(m.inline_call(call))  # type: ignore[attr-defined]
        except Exception:
            log.exception("inline Lua [&%s]", spec)
            return ""

    # ------------------------------------------------------------------ update cycle
    def update(self) -> None:
        """One skin tick: measures, meters, OnUpdateAction, layout, redraw."""
        if not self.loaded or self.closed:
            return
        self.counter += 1
        if self.rm.needs_read or self.rm.dynamic:
            self.rm.read()
        for m in list(self.measures.values()):
            m.update()
        for mt in self.meters:
            mt.update()
        self.layout()
        self.host.redraw(self)
        self.execute(self.rm.get("onupdateaction"), self.rm)

    def layout(self, force_size: bool = False) -> None:
        for mt in self.meters:
            mt.children = []
        for mt in self.meters:
            if mt.container_name:
                cont = self.meter(mt.container_name)
                if cont is not None and cont is not mt:
                    cont.children.append(mt)
        for mt in self.meters:
            mt.layout()
        if force_size or self.dynamic_window_size or self.counter <= 1 or not self.width:
            self._resize()

    def _resize(self) -> None:
        w = int(self.rm.get_number("skinwidth", 0)) if self.rm.has("skinwidth") else 0
        h = int(self.rm.get_number("skinheight", 0)) if self.rm.has("skinheight") else 0
        if not w or not h:
            mw = mh = 0
            for mt in self.meters:
                if mt.hidden or mt.container_name:
                    continue
                x, y, bw, bh = mt.box()
                mw = max(mw, int(x + bw + 0.999))
                mh = max(mh, int(y + bh + 0.999))
            bg = self.background_size()
            if bg:
                mw = max(mw, bg[0])
                mh = max(mh, bg[1])
            w = w or mw
            h = h or mh
        w, h = max(1, w), max(1, h)
        if (w, h) != (self.width, self.height):
            self.width, self.height = w, h
            self.host.resize(self, w, h)

    def background_size(self) -> tuple[int, int] | None:
        """Size of ``Background=`` for BackgroundMode 0 (image defines the window)."""
        bg = self.rm.get("background")
        mode = self.rm.get_int("backgroundmode", 0 if bg else 1)
        if not bg or mode != 0:
            return None
        from raintux.meters.images import load_image

        img = load_image(self.path(bg))
        return (img.width, img.height) if img else None

    def path(self, value: str) -> Path:
        """Resolve a skin path option (relative paths are relative to the skin folder)."""
        p = value.strip().strip('"').replace("\\", "/")
        if len(p) >= 2 and p[1] == ":":  # "C:/..." from a Windows skin — keep only the tail
            p = p[2:]
        path = Path(os.path.expanduser(p))
        return path if path.is_absolute() else self.root / path

    def draw(self, cr: Any) -> None:
        """Paint background and visible meters onto a Cairo context."""
        from raintux.meters.base_meter import draw_skin_background

        draw_skin_background(self, cr)
        for mt in self.meters:
            if not mt.hidden and not mt.container_name:
                mt.draw(cr)

    # ------------------------------------------------------------------ bangs
    def execute(self, action: str | None, source: Section | None = None) -> None:
        """Run an action string (``[!Bang …][…]``); section variables resolve now.

        Actions read from a section already had their ``#Variables#`` resolved when the
        option was read; external callers (API, command line) pass ``source=None`` and
        get them resolved here.
        """
        if not action or not action.strip():
            return
        if source is None:
            action = self.resolve(action)
        if self._depth > _MAX_ACTION_DEPTH:
            log.warning("action recursion limit reached in %s: %s", self.config, action[:80])
            return
        commands = split_commands(action)
        self._run_commands(commands, source)

    def _run_commands(self, commands: list[str], source: Section | None) -> None:
        self._depth += 1
        try:
            for i, cmd in enumerate(commands):
                if self.closed:
                    return
                cmd = self.replace_section_variables(cmd)
                if cmd.startswith("!"):
                    args = split_args(cmd[1:])
                    if not args:
                        continue
                    name = args[0].lower()
                    if name.startswith("rainmeter"):
                        name = name[len("rainmeter"):]
                    if name == "delay":
                        ms = int(parse_number(args[1] if len(args) > 1 else "0", 0))
                        rest = commands[i + 1 :]
                        self.host.call_later(max(0, ms), lambda: self._run_commands(rest, source))
                        return
                    try:
                        self.bang(name, args[1:], source)
                    except Exception:
                        log.exception("bang failed in %s: %s", self.config, cmd)
                else:
                    self.host.run_command(self, cmd)
        finally:
            self._depth -= 1

    def bang(self, name: str, args: list[str], source: Section | None = None) -> None:
        """Dispatch one bang (``name`` lower-case, without ``!``)."""
        if name in HOST_BANGS:
            self.host.host_bang(self, name, args)
            return
        argc = _LOCAL_ARGC.get(name)
        if argc is None:
            log.warning("unknown bang !%s in %s", name, self.config)
            return
        if name in _OPTIONAL_EXTRA:
            # Extra argument is a parameter (file path, log level), not a config name.
            argc_max = argc + 1
            if len(args) < argc:
                log.warning("!%s needs %d argument(s): %r", name, argc, args)
                return
            getattr(self, "_bang_" + name)(*args[:argc_max])
            return
        if len(args) > argc:
            target_config = args[argc]
            if target_config and target_config.replace("/", "\\").lower() != self.config.lower():
                other = self.host.find_skin(target_config)
                if other is None:
                    log.info("!%s: config %r is not active", name, target_config)
                else:
                    other.bang(name, args[:argc], None)
                return
        if len(args) < argc:
            log.warning("!%s needs %d argument(s): %r", name, argc, args)
            return
        getattr(self, "_bang_" + name)(*args[:argc])

    def _evaluated(self, value: str) -> str:
        v = value.strip()
        if v.startswith("(") and v.endswith(")"):
            try:
                return trim_number(evaluate(v, self.measure_number))
            except FormulaError:
                pass
        return value

    # skin-local bang implementations
    def _bang_setvariable(self, name: str, value: str) -> None:
        self.set_variable(name, self._evaluated(value))

    def _bang_setvariablegroup(self, name: str, value: str, group: str) -> None:
        self.set_variable(name, self._evaluated(value))

    def _bang_setoption(self, section: str, key: str, value: str) -> None:
        sec = self.section(section)
        if sec is None:
            log.info("!SetOption: no section [%s] in %s", section, self.config)
            return
        sec.set_option(key, value)

    def _bang_setoptiongroup(self, group: str, key: str, value: str) -> None:
        for sec in self._group(group, meters=True, measures=True):
            sec.set_option(key, value)

    def _bang_writekeyvalue(self, section: str, key: str, value: str, path: str | None = None) -> None:
        target = self.path(path) if path else self.ini_path
        write_key_value(target, section, key, self._evaluated(value))

    def _bang_update(self) -> None:
        self.update()

    def _bang_redraw(self) -> None:
        self.layout(force_size=True)
        self.host.redraw(self)

    def _bang_updatemeasure(self, name: str) -> None:
        for m in self._targets(name, self.measures.values()):
            m.update_now()  # type: ignore[attr-defined]

    def _bang_updatemeasuregroup(self, group: str) -> None:
        for m in self._group(group, measures=True):
            m.update_now()  # type: ignore[attr-defined]

    def _bang_updatemeter(self, name: str) -> None:
        for mt in self._targets(name, self.meters):
            mt.update(force=True)

    def _bang_updatemetergroup(self, group: str) -> None:
        for mt in self._group(group, meters=True):
            mt.update(force=True)  # type: ignore[call-arg]

    def _set_hidden(self, meters: Any, hidden: bool | None) -> None:
        for mt in meters:
            mt.hidden = (not mt.hidden) if hidden is None else hidden

    def _bang_showmeter(self, name: str) -> None:
        self._set_hidden(self._targets(name, self.meters), False)

    def _bang_hidemeter(self, name: str) -> None:
        self._set_hidden(self._targets(name, self.meters), True)

    def _bang_togglemeter(self, name: str) -> None:
        self._set_hidden(self._targets(name, self.meters), None)

    def _bang_showmetergroup(self, group: str) -> None:
        self._set_hidden(self._group(group, meters=True), False)

    def _bang_hidemetergroup(self, group: str) -> None:
        self._set_hidden(self._group(group, meters=True), True)

    def _bang_togglemetergroup(self, group: str) -> None:
        self._set_hidden(self._group(group, meters=True), None)

    def _set_measure_flag(self, measures: Any, attr: str, value: bool | None) -> None:
        for m in measures:
            setattr(m, attr, (not getattr(m, attr)) if value is None else value)

    def _bang_enablemeasure(self, name: str) -> None:
        self._set_measure_flag(self._targets(name, self.measures.values()), "disabled", False)

    def _bang_disablemeasure(self, name: str) -> None:
        self._set_measure_flag(self._targets(name, self.measures.values()), "disabled", True)

    def _bang_togglemeasure(self, name: str) -> None:
        self._set_measure_flag(self._targets(name, self.measures.values()), "disabled", None)

    def _bang_enablemeasuregroup(self, group: str) -> None:
        self._set_measure_flag(self._group(group, measures=True), "disabled", False)

    def _bang_disablemeasuregroup(self, group: str) -> None:
        self._set_measure_flag(self._group(group, measures=True), "disabled", True)

    def _bang_togglemeasuregroup(self, group: str) -> None:
        self._set_measure_flag(self._group(group, measures=True), "disabled", None)

    def _bang_pausemeasure(self, name: str) -> None:
        self._set_measure_flag(self._targets(name, self.measures.values()), "paused", True)

    def _bang_unpausemeasure(self, name: str) -> None:
        self._set_measure_flag(self._targets(name, self.measures.values()), "paused", False)

    def _bang_togglepausemeasure(self, name: str) -> None:
        self._set_measure_flag(self._targets(name, self.measures.values()), "paused", None)

    def _bang_pausemeasuregroup(self, group: str) -> None:
        self._set_measure_flag(self._group(group, measures=True), "paused", True)

    def _bang_unpausemeasuregroup(self, group: str) -> None:
        self._set_measure_flag(self._group(group, measures=True), "paused", False)

    def _bang_togglepausemeasuregroup(self, group: str) -> None:
        self._set_measure_flag(self._group(group, measures=True), "paused", None)

    def _bang_commandmeasure(self, name: str, args: str) -> None:
        m = self.measure(name)
        if m is None:
            log.info("!CommandMeasure: no measure [%s] in %s", name, self.config)
            return
        m.command(args)

    def _bang_pluginbang(self, text: str) -> None:
        name, _, rest = text.strip().partition(" ")
        self._bang_commandmeasure(name, rest.strip())

    def _bang_movemeter(self, x: str, y: str, name: str) -> None:
        mt = self.meter(name)
        if mt is not None:
            mt.set_option("x", x)
            mt.set_option("y", y)

    def _bang_log(self, message: str, level: str = "notice") -> None:
        lvl = {"error": logging.ERROR, "warning": logging.WARNING, "debug": logging.DEBUG}.get(level.lower(), logging.INFO)
        log.log(lvl, "[%s] %s", self.config, message)

    def _bang_execute(self, actions: str) -> None:
        self.execute(actions)

    def _targets(self, name: str, items: Any) -> list[Any]:
        n = name.strip().lower()
        if n == "*":
            return list(items)
        return [s for s in items if s.name.lower() == n]

    def _group(self, group: str, meters: bool = False, measures: bool = False) -> list[Section]:
        out: list[Section] = []
        if measures:
            out += [m for m in self.measures.values() if m.in_group(group)]
        if meters:
            out += [m for m in self.meters if m.in_group(group)]
        return out

    # ------------------------------------------------------------------ mouse
    def meter_at(self, x: float, y: float, action: str | None = None) -> Meter | None:
        for mt in reversed(self.meters):
            if mt.hidden or not mt.hit_test(x, y):
                continue
            if action is None or mt.mouse_action(action):
                return mt
        return None

    def mouse(self, kind: str, x: float = -1, y: float = -1, button: str = "left") -> bool:
        """Deliver pointer input. Returns True when an action (or button) consumed it.

        ``kind``: ``move``, ``leave``, ``down``, ``up``, ``double``, ``scrollup``,
        ``scrolldown``, ``scrollleft``, ``scrollright``.
        """
        if kind == "move":
            self._mouse_move(x, y)
            return False
        if kind == "leave":
            self._mouse_leave()
            return False
        from raintux.meters.button_meter import ButtonMeter

        if button == "left" and kind in ("down", "double"):
            hit = next((m for m in reversed(self.meters) if not m.hidden and m.hit_test(x, y)), None)
            if isinstance(hit, ButtonMeter):
                hit.pressed = True
                self._pressed_button = hit
                self.host.redraw(self)
                return True
        if button == "left" and kind == "up" and self._pressed_button is not None:
            btn = self._pressed_button
            self._pressed_button = None
            btn.pressed = False
            if btn.hit_test(x, y):
                self.execute(btn.get("buttoncommand"), btn)
            self.host.redraw(self)
            return True

        if kind.startswith("scroll"):
            names = ["mouse" + kind + "action"]
        elif kind == "double":
            names = [f"{button}mousedoubleclickaction", f"{button}mousedownaction"]
        else:
            names = [f"{button}mouse{kind}action"]
        for action in names:
            mt = self.meter_at(x, y, action)
            if mt is not None:
                self.execute(mt.mouse_action(action), mt)
                return True
            skin_action = self.rm.get(action)
            if skin_action:
                self.execute(skin_action, self.rm)
                return True
        return False

    def _mouse_move(self, x: float, y: float) -> None:
        if not self._mouse_over_skin:
            self._mouse_over_skin = True
            self.execute(self.rm.get("mouseoveraction"), self.rm)
        from raintux.meters.button_meter import ButtonMeter

        now: set[str] = set()
        top_button: Meter | None = None
        cursor_meter: Meter | None = None
        tooltip_meter: Meter | None = None
        for mt in reversed(self.meters):
            if mt.hidden or not mt.hit_test(x, y):
                continue
            if isinstance(mt, ButtonMeter) and top_button is None:
                top_button = mt
            if cursor_meter is None and mt.has_mouse_actions():
                cursor_meter = mt
            if tooltip_meter is None and mt.get("tooltiptext"):
                tooltip_meter = mt
            if mt.mouse_action("mouseoveraction") or mt.mouse_action("mouseleaveaction") or isinstance(mt, ButtonMeter):
                now.add(mt.name.lower())
        changed = False
        for mt in self.meters:
            if isinstance(mt, ButtonMeter):
                focus = mt is top_button
                if mt.focused != focus:
                    mt.focused = focus
                    changed = True
        for name in now - self._hover:
            mt = self.meter_map[name]
            self.execute(mt.mouse_action("mouseoveraction"), mt)
        for name in self._hover - now:
            mt = self.meter_map.get(name)
            if mt is not None:
                self.execute(mt.mouse_action("mouseleaveaction"), mt)
        self._hover = now
        cursor = None
        if cursor_meter is not None and cursor_meter.get_bool("mouseactioncursor", self.rm.get_bool("mouseactioncursor", True)):
            cursor = cursor_meter.get("mouseactioncursorname", "pointer") or "pointer"
        tip = None
        if tooltip_meter is not None and not self.rm.get_bool("tooltiphidden"):
            tip = tooltip_meter.get("tooltiptext").replace("#CRLF#", "\n")
            title = tooltip_meter.get("tooltiptitle")
            if title:
                tip = f"{title}\n{tip}"
        self.host.set_pointer(self, cursor, tip)
        if changed:
            self.host.redraw(self)

    def _mouse_leave(self) -> None:
        from raintux.meters.button_meter import ButtonMeter

        for name in list(self._hover):
            mt = self.meter_map.get(name)
            if mt is not None:
                if isinstance(mt, ButtonMeter):
                    mt.focused = False
                self.execute(mt.mouse_action("mouseleaveaction"), mt)
        self._hover = set()
        if self._mouse_over_skin:
            self._mouse_over_skin = False
            self.execute(self.rm.get("mouseleaveaction"), self.rm)
        self.host.set_pointer(self, None, None)

    def input_rects(self) -> list[tuple[int, int, int, int]]:
        """Areas that take pointer input; everything else clicks through (like Rainmeter's alpha test)."""
        if self.rm.get("background") or self.rm.get_int("backgroundmode", 1) == 2:
            return [(0, 0, self.width, self.height)]
        rects = []
        for mt in self.meters:
            if mt.hidden or mt.container_name:
                continue
            x, y, w, h = mt.box()
            if w > 0 and h > 0:
                rects.append((int(x), int(y), int(w + 0.999), int(h + 0.999)))
        return rects

    def context_items(self) -> list[tuple[str, str]]:
        """``ContextTitle`` / ``ContextAction`` pairs from ``[Rainmeter]``."""
        items = []
        i = 1
        while True:
            sfx = "" if i == 1 else str(i)
            title = self.rm.get("contexttitle" + sfx)
            if not title and not self.rm.has("contexttitle" + sfx):
                break
            items.append((title, self.rm.get("contextaction" + sfx)))
            i += 1
        return items


def _char_ref(m: re.Match[str]) -> str:
    code = m.group(1)
    try:
        n = int(code[1:], 16) if code[0] in "xX" else int(code)
        return chr(n)
    except (ValueError, OverflowError):
        return m.group(0)
