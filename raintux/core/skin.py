"""One loaded skin: variables, measures, meters, the update cycle, bangs and mouse input.

This module has no GTK dependency. Everything that touches the desktop (windows,
timers, running programs, other skins) goes through a :class:`SkinHost`, so the
whole Rainmeter behaviour can be unit-tested with :class:`NullHost`.
"""

from __future__ import annotations

import logging
import math
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
from raintux.core.paths import environment_variable, expand_environment
from raintux.core.skin_parser import ParsedSkin, expand_variables, find_resources, parse_skin_ini, variable_table
from raintux.measures import create_measure
from raintux.measures.base_measure import Measure, format_number, trim_number

if TYPE_CHECKING:
    from raintux.meters.base_meter import Meter

log = logging.getLogger(__name__)

_SECTION_VAR = re.compile(r"\[([^\[\]]+)\]")
_CHAR_REF = re.compile(r"\[\\(x[0-9a-fA-F]{1,6}|\d{1,7})\]")
_MAX_ACTION_DEPTH = 32
_MAX_WINDOW = 16384  # px; larger windows can't be allocated by GTK/cairo anyway

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
    "setzoomfactor", "autoselectscreen", "autoselectscreengroup", "restart", "debug", "skincustommenu",
    "updategroup", "redrawgroup", "showfadegroup", "hidefadegroup", "togglefadegroup", "sendkey",
    "disablemouseactionskingroup", "enablemouseactionskingroup", "togglemouseactionskingroup",
    "clearmouseactionskingroup", "forceenablevisiblemode",
}

# "!Skin:Load" style aliases (current Rainmeter).
SKIN_ALIASES = {"skin:load": "activateconfig", "skin:unload": "deactivateconfig", "skin:toggle": "toggleconfig",
                "skin:edit": "editskin"}

# Section bangs registered by measures/meters ("!ActionTimer:Execute 1"): name -> argument count
# (one extra leading argument names the section, or "Group=Name"; otherwise the current section).
SECTION_BANGS: dict[str, int] = {
    "actiontimer:execute": 1, "actiontimer:stop": 1,
    "audio:setvolume": 1, "audio:setoutputindex": 1, "audio:mute": 0, "audio:unmute": 0, "audio:togglemute": 0,
    "recyclemanager:emptybin": 0, "recyclemanager:emptybinsilent": 0, "recyclemanager:openbin": 0,
    "loop:reset": 0, "mouse:startcapture": 0, "mouse:stopcapture": 0,
    "webparser:refetch": 0, "webparser:clearresults": 0,
    "runcommand:run": 0, "runcommand:close": 0, "runcommand:kill": 0,
    "nowplaying:play": 0, "nowplaying:pause": 0, "nowplaying:toggleplay": 0, "nowplaying:stop": 0,
    "nowplaying:next": 0, "nowplaying:previous": 0, "nowplaying:openplayer": 0, "nowplaying:closeplayer": 0,
    "nowplaying:toggleplayer": 0, "nowplaying:setposition": 1, "nowplaying:setrating": 1,
    "nowplaying:setvolume": 1, "nowplaying:setshuffle": 1, "nowplaying:setrepeat": 1,
    "textedit:focus": 0, "textedit:submit": 0, "textedit:select": 2, "textedit:selectall": 0,
    "textedit:settext": 1, "textedit:scrollbyline": 1, "textedit:resetscroll": 0, "textedit:clear": 0,
    "textedit:reset": 0, "inputtext:executebatch": 1, "windowmessage:send": 3,
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
    "log": 1, "execute": 1, "delay": 1, "setupdate": 1, "commandmeasuregroup": 2,
    "disablemouseaction": 2, "enablemouseaction": 2, "togglemouseaction": 2, "clearmouseaction": 2,
    "disablemouseactiongroup": 2, "enablemouseactiongroup": 2, "togglemouseactiongroup": 2,
    "clearmouseactiongroup": 2,
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
    def ask_text(self, skin: Skin, rect: tuple[float, float, float, float], style: dict[str, Any], default: str,
                 password: bool, callback: Callable[[str | None], None]) -> None: ...


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
        self.prompts: list[tuple[Any, str, Callable[[str | None], None]]] = []
        self._soon: list[Callable[[], None]] = []
        import threading

        self._lock = threading.Lock()

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
        with self._lock:
            self._soon.append(fn)

    def drain(self, timeout: float = 0.0) -> int:
        """Run callbacks queued by worker threads, waiting up to ``timeout`` s for the first."""
        import time

        deadline = time.monotonic() + timeout
        ran = 0
        while True:
            with self._lock:
                batch, self._soon = self._soon, []
            for fn in batch:
                fn()
                ran += 1
            if ran or time.monotonic() >= deadline:
                return ran
            time.sleep(0.01)

    def ask_text(self, skin: Skin, rect: tuple[float, float, float, float], style: dict[str, Any], default: str,
                 password: bool, callback: Callable[[str | None], None]) -> None:
        self.prompts.append((rect, default, callback))

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
        self._inline_errors: set[str] = set()
        self._laid_out = False  # until then [Meter:X] walks the relative chain (_layout_chain)
        self._vars_version = 0  # bumped on every variable change; keys the #Variable# table cache
        self._var_cache: tuple[tuple[int, int, int, int, int], dict[str, str]] | None = None
        self.on_size_changed: Callable[[int, int], None] | None = None
        self._mouse_ctx: tuple[float, float, Any] | None = None
        self.zoom = 1.0
        self.focused_input: Any = None  # TextEdit meter holding the caret
        self._selecting_input: Any = None  # TextEdit being drag-selected

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        """Parse the INI and create sections (no update yet)."""
        from raintux.meters import create_meter

        self._laid_out = False
        self.parsed = parse_skin_ini(self.ini_path, self.resources, self.builtins())
        self.variables = OrderedDict((k.lower(), v) for k, v in self.parsed.variables.items())
        self._vars_version += 1
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
                "PROGRAMDRIVE": "C:",  # FreeDiskSpace maps C: to /
                "ADDONSPATH": str(self.skins_root.parent / "Addons") + "/",
                "PLUGINSPATH": str(self.skins_root.parent / "Plugins") + "/",
                "CONFIGEDITOR": os.environ.get("VISUAL") or os.environ.get("EDITOR") or "xdg-open",
                "CRLF": "\n",
            }
            # Defaults so skins never see a raw #SCREENAREAX# when the host knows no monitors.
            for prefix in ("SCREENAREA", "WORKAREA", "PSCREENAREA", "PWORKAREA", "VSCREENAREA"):
                b.update({prefix + "X": "0", prefix + "Y": "0", prefix + "WIDTH": "1920", prefix + "HEIGHT": "1080"})
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
        self._var_cache = None

    def _variable_table(self) -> dict[str, str]:
        """Merged [Variables] + built-ins, rebuilt only when something they depend on changed."""
        key = (self._vars_version, self.state.x, self.state.y, self.width, self.height)
        if self._var_cache is None or self._var_cache[0] != key:
            self._var_cache = (key, variable_table(self.variables, self.root, self.resources, self.builtins()))
        return self._var_cache[1]

    def get_variable(self, name: str) -> str | None:
        n = name.strip().lower()
        if n in self.variables:
            return self.resolve(self.variables[n])
        b = self.builtins()
        return b.get(name.strip().upper())

    def set_variable(self, name: str, value: str) -> None:
        self.variables[name.strip().lower()] = value
        self._vars_version += 1

    def resolve(self, text: str, section_vars: bool = False) -> str:
        """Expand ``#Variables#``, character references and (optionally) ``[Section]`` variables."""
        if not text:
            return text
        out = expand_variables(expand_environment(text), self._variable_table())
        if "[" in out:
            out = _CHAR_REF.sub(_char_ref, out)
            if section_vars:
                out = self.replace_section_variables(out)
        return out

    def replace_section_variables(self, text: str, current: Section | None = None) -> str:
        placeholders: list[str] = []

        def repl(m: re.Match[str]) -> str:
            inner = m.group(1)
            if len(inner) > 2 and inner.startswith("*") and inner.endswith("*"):
                placeholders.append("[" + inner[1:-1] + "]")
                return f"\0{len(placeholders) - 1}\0"
            value = self.section_variable(inner, current)
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

    def section_variable(self, spec: str, current: Section | None = None) -> str | None:
        """Value for ``[Name]``, ``[Name:%,1]``, ``[Name:/1024,2]``, ``[Meter:W]`` and the keyed forms
        ``[#Var]``, ``[&Measure]`` / ``[&Script:func()]``, ``[%ENV]``, ``[$SkinX]``, ``[$Input]``."""
        if spec.startswith("#"):
            return self.get_variable(spec[1:])
        if spec.startswith("&"):
            rest = spec[1:]
            name, colon, call = rest.partition(":")
            m = self.measure(name)
            if m is not None and colon and "(" in call and hasattr(m, "inline_call"):
                return self._inline_script(rest)
            return self.section_variable(rest, current)
        if spec.startswith("%"):
            return environment_variable(spec[1:])
        if spec.startswith("$"):
            return self.dollar_variable(spec[1:], current)
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
            if not self._laid_out:
                self._layout_chain(mt)
            x, y, _w, _h = mt.box()
            w, h = mt.visible_size()
            vals = {"X": x, "Y": y, "W": w, "H": h, "XW": x + w, "YH": y + h}
            if key in vals:
                return trim_number(vals[key])
        return None

    def dollar_variable(self, spec: str, current: Section | None = None) -> str | None:
        """``[$SkinX]`` / ``[$SkinZoomFactor]`` / ``[$Input]`` / ``[$MouseX]`` (mouse only in mouse actions)."""
        low = spec.lower()
        if low.startswith("mouse"):
            ctx = self._mouse_ctx
            return self.mouse_variable(spec, ctx[0], ctx[1], ctx[2]) if ctx else None
        if low.startswith("input"):
            target = current if current is not None and hasattr(current, "input_variable") else None
            return target.input_variable(spec[5:]) if target is not None else None  # type: ignore[attr-defined]
        st = self.state
        skin_vars = {
            "skinx": st.x, "skiny": st.y, "skinw": self.width, "skinh": self.height,
            "skinphysicalx": st.x, "skinphysicaly": st.y, "skinphysicalw": int(self.width * self.zoom),
            "skinphysicalh": int(self.height * self.zoom), "skinzoomedw": int(self.width * self.zoom),
            "skinzoomedh": int(self.height * self.zoom), "skinzpos": st.z, "skindpifactor": 1,
            "skinzoomfactor": self.zoom, "skinvisible": 1,
        }
        if low in skin_vars:
            return trim_number(float(skin_vars[low]))
        return None

    def mouse_variable(self, spec: str, x: float, y: float, meter: Any) -> str | None:
        """``MouseX``/``MouseY`` relative to the acting meter (or skin), ``:%`` for percent."""
        low = spec.lower().replace(" ", "")
        if low not in ("mousex", "mousey", "mousex:%", "mousey:%"):
            return None
        horizontal = low.startswith("mousex")
        if meter is not None and hasattr(meter, "box"):
            bx, by, bw, bh = meter.box()
        else:
            bx, by, bw, bh = 0.0, 0.0, float(self.width), float(self.height)
        pos = (x - bx) if horizontal else (y - by)
        if low.endswith("%"):
            size = bw if horizontal else bh
            return str(int((pos + 1) / size * 100)) if size else "0"
        return str(int(pos))

    def with_mouse_variables(self, action: str, x: float, y: float, meter: Any) -> str:
        """Replace ``$MouseX$``-style variables (``$*MouseX*$`` escapes) before running a mouse action."""
        if not action or "$" not in action:
            return action

        def repl(m: re.Match[str]) -> str:
            inner = m.group(1)
            if len(inner) > 2 and inner.startswith("*") and inner.endswith("*"):
                return "$" + inner[1:-1] + "$"
            v = self.mouse_variable(inner, x, y, meter)
            return m.group(0) if v is None else v

        return re.sub(r"\$([^$\s\[\]]+)\$", repl, action)

    def run_mouse_action(self, action: str, x: float, y: float, meter: Any, source: Section) -> None:
        """Execute a mouse action with ``$MouseX$`` / ``[$MouseX]`` bound to this event."""
        if not action:
            return
        prev = self._mouse_ctx
        self._mouse_ctx = (x, y, meter)
        try:
            self.execute(self.with_mouse_variables(action, x, y, meter), source)
        finally:
            self._mouse_ctx = prev

    def _inline_script(self, spec: str) -> str | None:
        """``[&ScriptMeasure:func('arg')]`` → call into a Lua script measure."""
        name, _, call = spec.partition(":")
        m = self.measure(name)
        if m is None or not hasattr(m, "inline_call"):
            return None
        try:
            return str(m.inline_call(call))  # type: ignore[attr-defined]
        except Exception as e:
            if spec not in self._inline_errors:  # once per call site, not on every update
                self._inline_errors.add(spec)
                log.error("[&%s] in %s: %s", spec, self.config, str(e).strip().splitlines()[0] if str(e).strip() else e)
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

    def _layout_chain(self, mt: Meter) -> None:
        """Before the first layout, position ``mt`` from its relative chain on demand, like
        Rainmeter's Meter::GetX/GetY (``Y=([Other:Y]+60)`` read while the skin loads)."""
        chain: list[Meter] = []
        cur: Meter | None = mt
        while cur is not None and len(chain) < 10000:
            chain.append(cur)
            cur = cur.relative_to
        for m in reversed(chain):
            m.layout()

    def layout(self, force_size: bool = False) -> None:
        # Relative meters (Rainmeter's UpdateRelativeMeters): contained meters chain inside their
        # container; everything else chains to the previous non-contained meter (hidden ones too).
        for mt in self.meters:
            mt.children = []
        last_in: dict[int, Any] = {}
        prev: Meter | None = None
        for mt in self.meters:
            cont = self.meter(mt.container_name) if mt.container_name else None
            if cont is not None and cont is not mt:
                cont.children.append(mt)
                mt.relative_to = last_in.get(id(cont), cont)
                last_in[id(cont)] = mt
                continue
            mt.relative_to = prev
            prev = mt
        for mt in self.meters:
            mt.layout()
        self._laid_out = True
        if force_size or self.dynamic_window_size or self.counter <= 1 or not self.width:
            self._resize()

    def _resize(self) -> None:
        w = int(self.rm.get_number("skinwidth", 0)) if self.rm.has("skinwidth") else 0
        h = int(self.rm.get_number("skinheight", 0)) if self.rm.has("skinheight") else 0
        if not w or not h:
            # Rainmeter's ResizeWindow: meters (hidden ones at size 0) + BackgroundMargins, at least
            # the background image; BackgroundMode=0 is exactly the image.
            from raintux.meters.base_meter import parse_numbers

            ml, mt_, mr, mb = (int(v) for v in parse_numbers(self.rm.get("backgroundmargins"), 4))
            mw, mh = ml, mt_
            for mt in self.meters:
                if mt.container_name and self.meter(mt.container_name) is not None:
                    continue
                x, y, _bw, _bh = mt.box()
                vw, vh = mt.visible_size()
                right, bottom = x + vw, y + vh
                if not (math.isfinite(right) and math.isfinite(bottom)) or max(right, bottom) > _MAX_WINDOW:
                    continue  # runaway formula, e.g. dividing by a value a failed download left at 0
                mw = max(mw, int(right + 0.999))
                mh = max(mh, int(bottom + 0.999))
            mw += mr
            mh += mb
            bg = self.background_size()
            if bg:
                if self.rm.get_int("backgroundmode", 0) == 0:
                    mw, mh = bg
                else:
                    mw, mh = max(mw, bg[0]), max(mh, bg[1])
            w = w or mw
            h = h or mh
        w, h = min(max(1, w), _MAX_WINDOW), min(max(1, h), _MAX_WINDOW)
        if (w, h) != (self.width, self.height):
            self.width, self.height = w, h
            self.host.resize(self, w, h)

    def background_size(self) -> tuple[int, int] | None:
        """Size of the ``Background=`` image (modes 0, 3 and 4 use an image)."""
        bg = self.rm.get("background")
        mode = self.rm.get_int("backgroundmode", 0 if bg else 1)
        if not bg or mode not in (0, 3, 4):
            return None
        from raintux.meters.images import load_image

        img = load_image(self.path(bg))
        return (img.width, img.height) if img else None

    def path(self, value: str) -> Path:
        """Resolve a skin path option: relative to the skin folder, backslashes and
        file-name case as Windows would accept them."""
        from raintux.core.paths import resolve_ci, windows_to_posix

        path = Path(os.path.expanduser(windows_to_posix(value)))
        return resolve_ci(path if path.is_absolute() else self.root / path)

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
                if not cmd.startswith("!"):
                    # A program to run, or a whole bang from a variable ([#MyBang]).
                    cmd = self.replace_section_variables(cmd, source)
                    if cmd.startswith("["):
                        self._run_commands(split_commands(cmd), source)
                        continue
                if cmd.startswith("!"):
                    # Like Rainmeter's CommandHandler: split first, then resolve [Section]
                    # variables per argument, so a value like "497.2 M" stays one argument.
                    args = [self.replace_section_variables(a, source) for a in split_args(cmd[1:])]
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
        if name in SKIN_ALIASES:
            name = SKIN_ALIASES[name]
        if ":" in name:
            if name.startswith("skin:"):
                self._skin_prefixed_bang(name[5:], args)
            else:
                self._section_bang(name, args, source)
            return
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

    def _section_bang(self, name: str, args: list[str], source: Section | None) -> None:
        """``!Type:Action [Section|Group=Name] args…`` handled by the section's ``command``."""
        argc = SECTION_BANGS.get(name)
        if argc is None:
            log.warning("unknown bang !%s in %s", name, self.config)
            return
        namespace, _sep, action = name.partition(":")
        targets: list[Section] = []
        if len(args) > argc:
            target, args = args[0], args[1:]
            if target.lower().startswith("group="):
                targets = self._group(target[6:], meters=True, measures=True)
            else:
                sec = self.section(target)
                if sec is None:
                    log.warning("!%s: [%s] not found in %s", name, target, self.config)
                    return
                targets = [sec]
        elif source is not None:
            targets = [source]
        else:
            log.warning("!%s: section name required", name)
            return
        for sec in targets:
            if getattr(sec, "bang_namespace", None) != namespace:
                log.warning("!%s: invalid bang for [%s]", name, sec.name)
                continue
            sec.command(" ".join([action, *args]).strip())  # type: ignore[attr-defined]

    def _skin_prefixed_bang(self, bang: str, args: list[str]) -> None:
        """``!Skin:Refresh "Config"`` / ``"Group=Name"``: run ``bang`` on other skins."""
        if not args:
            log.warning("!Skin:%s needs a config name", bang)
            return
        target, rest = args[0], args[1:]
        skins: list[Skin] = []
        if target.lower().startswith("group="):
            group = target[6:].strip().lower()
            all_skins = getattr(self.host, "all_skins", None)
            skins = [s for s in (all_skins() if all_skins else [self]) if group in s.config_groups()]
        else:
            other = self.host.find_skin(target)
            if other is None:
                log.info("!Skin:%s: config %r is not active", bang, target)
            else:
                skins = [other]
        for sk in skins:
            sk.bang(bang.lower(), rest, None)

    def config_groups(self) -> set[str]:
        return {g.strip().lower() for g in self.rm.get("group").split("|") if g.strip()}

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
            mt.set_hidden((not mt.hidden) if hidden is None else hidden)

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

    def _bang_setupdate(self, value: str) -> None:
        self.rm.set_option("update", value)
        self.rm.read()

    def _bang_commandmeasuregroup(self, args: str, group: str) -> None:
        for m in self._group(group, measures=True):
            m.command(args)  # type: ignore[attr-defined]

    def _mouse_action_change(self, meters: Any, actions: str, mode: str) -> None:
        names = [a.strip().lower() for a in actions.split("|") if a.strip()]
        for mt in meters:
            for a in names:
                if mode == "disable":
                    mt.disabled_actions.add(a)
                elif mode == "enable":
                    mt.disabled_actions.discard(a)
                elif mode == "toggle":
                    mt.disabled_actions ^= {a}
                else:
                    mt.cleared_actions.add(a)

    def _bang_disablemouseaction(self, meter: str, actions: str) -> None:
        self._mouse_action_change(self._targets(meter, self.meters), actions, "disable")

    def _bang_enablemouseaction(self, meter: str, actions: str) -> None:
        self._mouse_action_change(self._targets(meter, self.meters), actions, "enable")

    def _bang_togglemouseaction(self, meter: str, actions: str) -> None:
        self._mouse_action_change(self._targets(meter, self.meters), actions, "toggle")

    def _bang_clearmouseaction(self, meter: str, actions: str) -> None:
        self._mouse_action_change(self._targets(meter, self.meters), actions, "clear")

    def _bang_disablemouseactiongroup(self, group: str, actions: str) -> None:
        self._mouse_action_change(self._group(group, meters=True), actions, "disable")

    def _bang_enablemouseactiongroup(self, group: str, actions: str) -> None:
        self._mouse_action_change(self._group(group, meters=True), actions, "enable")

    def _bang_togglemouseactiongroup(self, group: str, actions: str) -> None:
        self._mouse_action_change(self._group(group, meters=True), actions, "toggle")

    def _bang_clearmouseactiongroup(self, group: str, actions: str) -> None:
        self._mouse_action_change(self._group(group, meters=True), actions, "clear")

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
        """Deliver pointer input. Returns True when the skin consumed it (no window drag).

        ``kind``: ``move``, ``drag`` (motion with a button held), ``leave``, ``down``, ``up``,
        ``double``, ``scrollup``, ``scrolldown``, ``scrollleft``, ``scrollright``.
        """
        from raintux.measures.plugins.mouse import mouse_measures
        from raintux.meters.button_meter import ButtonMeter
        from raintux.meters.text_edit_meter import TextEditMeter

        if kind == "move":
            self._mouse_move(x, y)
            for mm in mouse_measures(self):
                mm.moved(x, y)
            return False
        if kind == "drag":
            consumed = False
            if self._selecting_input is not None:
                mt = self._selecting_input
                bx, by, _w, _h = mt.box()
                mt.move(mt.index_at(x - bx - mt.padding[0], y - by - mt.padding[1]), True)
                consumed = True
            for mm in mouse_measures(self):
                if mm.dragging:
                    mm.moved(x, y)
                    consumed = True
            return consumed
        if kind == "leave":
            self._mouse_leave()
            return False

        consumed = False
        if kind in ("down", "double"):
            hit = next((m for m in reversed(self.meters) if not m.hidden and m.hit_test(x, y)), None)
            focused = self.focused_input
            if focused is not None and hit is not focused:
                focused.dismiss()
            if button == "left" and isinstance(hit, TextEditMeter) and hit.accepts_input:
                hit.focus()
                bx, by, _w, _h = hit.box()
                idx = hit.index_at(x - bx - hit.padding[0], y - by - hit.padding[1])
                if kind == "double":
                    hit.select_word_at(idx)
                else:
                    hit.move(idx, False)
                    self._selecting_input = hit
                return True
            if button == "left" and isinstance(hit, ButtonMeter):
                hit.pressed = True
                self._pressed_button = hit
                self.host.redraw(self)
                return True
            for mm in mouse_measures(self):
                consumed = mm.pressed(button, x, y) or consumed
        if kind == "up":
            if button == "left" and self._selecting_input is not None:
                self._selecting_input = None
                return True
            for mm in mouse_measures(self):
                mm.released(button, x, y)
            if button == "left" and self._pressed_button is not None:
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
                self.run_mouse_action(mt.mouse_action(action), x, y, mt, mt)
                return True
            skin_action = self.rm.get(action)
            if skin_action:
                self.run_mouse_action(skin_action, x, y, None, self.rm)
                return True
        return consumed

    def key(self, key: str, ctrl: bool = False, shift: bool = False) -> bool:
        """Editing key for the focused TextEdit (GDK key name). Returns True when handled."""
        mt = self.focused_input
        return bool(mt is not None and mt.handle_key(key, ctrl, shift))

    def text_input(self, text: str) -> bool:
        """Committed text (typing, IME, paste) for the focused TextEdit."""
        mt = self.focused_input
        return bool(mt is not None and mt.insert(text))

    def keyboard_focus_lost(self) -> None:
        mt = self.focused_input
        if mt is not None:
            mt.dismiss()

    def drop_motion(self, x: float, y: float) -> bool:
        from raintux.measures.plugins.dragdrop import drop_measures

        accepted = False
        for m in drop_measures(self):
            accepted = m.drag_motion(x, y) or accepted
        return accepted

    def drop_leave(self) -> None:
        from raintux.measures.plugins.dragdrop import drop_measures

        for m in drop_measures(self):
            m.drag_leave()

    def drop_files(self, files: list[Path], x: float, y: float) -> bool:
        from raintux.measures.plugins.dragdrop import drop_measures

        handled = False
        for m in drop_measures(self):
            handled = m.dropped(files, x, y) or handled
        return handled

    def accepts_drops(self) -> bool:
        from raintux.measures.plugins.dragdrop import drop_measures

        return bool(drop_measures(self))

    def wants_keyboard(self) -> bool:
        return self.focused_input is not None

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
            if cursor_meter is None and (mt.has_mouse_actions() or getattr(mt, "accepts_input", False)):
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
            self.run_mouse_action(mt.mouse_action("mouseoveraction"), x, y, mt, mt)
        for name in self._hover - now:
            mt = self.meter_map.get(name)
            if mt is not None:
                self.run_mouse_action(mt.mouse_action("mouseleaveaction"), x, y, mt, mt)
        self._hover = now
        cursor = None
        if getattr(cursor_meter, "accepts_input", False):
            cursor = "text"
        elif cursor_meter is not None and cursor_meter.get_bool("mouseactioncursor", self.rm.get_bool("mouseactioncursor", True)):
            name = cursor_meter.get("mouseactioncursorname", "hand").strip()
            custom = self.resources / "Cursors" / name
            if "." in name and not name.lower().endswith(".ani"):
                custom_path = self.path(str(custom))
                cursor = ("file:" + str(custom_path)) if custom_path.is_file() else "pointer"
            else:
                cursor = _cursor_name(name)
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


_CURSORS = {
    "hand": "pointer", "text": "text", "help": "help", "busy": "wait", "cross": "crosshair",
    "pen": "crosshair", "no": "not-allowed", "size_all": "move", "size_nesw": "nesw-resize",
    "size_ns": "ns-resize", "size_nwse": "nwse-resize", "size_we": "ew-resize", "uparrow": "default",
    "wait": "progress", "arrow": "default",
}


def _cursor_name(rainmeter_name: str) -> str:
    """Rainmeter ``MouseActionCursorName`` → CSS cursor name (custom .cur files fall back to hand)."""
    return _CURSORS.get(rainmeter_name.strip().lower(), "pointer")


def _char_ref(m: re.Match[str]) -> str:
    code = m.group(1)
    try:
        n = int(code[1:], 16) if code[0] in "xX" else int(code)
        return chr(n)
    except (ValueError, OverflowError):
        return m.group(0)
