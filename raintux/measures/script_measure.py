"""``Measure=Script``: Lua scripts with Rainmeter's SKIN / SELF API (via ``lupa``).

Supported: ``Initialize()``, ``Update()`` (number or string result), ``!CommandMeasure``
with Lua code, inline ``[&Script:func()]`` section variables, ``print``, ``tolua.cast``, and
SKIN:Bang / GetMeasure / GetMeter / SetOption / SetOptionGroup / GetVariable / SetVariable /
ReplaceVariables / ParseFormula / MoveWindow / FadeWindow / MakePathAbsolute / ReadTextFile /
WriteTextFile / GetX / GetY / GetW / GetH; measure objects (GetValue, GetStringValue,
GetRelativeValue, GetMinValue, GetMaxValue, GetValueRange, GetOption, SetOption,
GetNumberOption, GetName, Enable, Disable) and meter objects (GetOption, SetOption,
GetNumberOption, GetName, GetX/Y/W/H, SetX/Y/W/H, Show, Hide, SetText). SetOption and
SetVariable take ``(name, value)`` or any number of ``{name, value}`` tables. io.open, io.lines,
dofile, loadfile and require take paths as Windows does: backslashes and any letter case.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from raintux.core.formula import FormulaError, evaluate, parse_number
from raintux.core.ini_writer import detect_encoding
from raintux.core.paths import resolve_ci, windows_to_posix
from raintux.measures.base_measure import Measure, trim_number


def _lua_str(v: Any) -> str:
    """Lua's tostring for values passed to Python: Lua 5.1 numbers are doubles, ``5`` not ``5.0``."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else format(v, ".14g")
    return str(v)


def _lua_int(v: Any) -> int:
    """C's ``(int)lua_tonumber(L, n)``: truncated toward zero."""
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return 0


log = logging.getLogger(__name__)

# Lua side of the API: thin tables whose methods call back into Python (``py``).
_PRELUDE = r"""
local py = ...
-- lua_isstring: strings and numbers.
local function isstr(v) return type(v) == 'string' or type(v) == 'number' end
-- LuaHelper::ToWide: a string or number as text, anything else as ''.
local function wstr(v) if isstr(v) then return tostring(v) end return '' end
-- LuaSection::SetOption / SKIN:SetVariable arguments: (name, value) or any number of
-- {name, value} tables; a pair whose name is not a string or value not a string/number is skipped.
local function each_pair(fn, ...)
  if type((...)) == 'table' then
    for i = 1, select('#', ...) do
      local t = select(i, ...)
      if type(t) == 'table' then
        local k, v = rawget(t, 1), rawget(t, 2)
        if type(k) == 'string' and isstr(v) then fn(k, tostring(v)) end
      end
    end
  else
    local k, v = ...
    if isstr(k) and isstr(v) then fn(tostring(k), tostring(v)) end
  end
end
local function wrap_measure(h)
  if h == nil then return nil end
  local o = {}
  function o:GetValue() return py.mval(h) end
  function o:GetStringValue() return py.mstr(h) end
  function o:GetRelativeValue() return py.mrel(h) end
  function o:GetMinValue() return py.mmin(h) end
  function o:GetMaxValue() return py.mmax(h) end
  function o:GetValueRange() return py.mmax(h) - py.mmin(h) end
  function o:GetOption(k, d) return py.opt(h, k, d or '') end
  function o:SetOption(...) each_pair(function(k, v) py.setoption(h, k, v, false) end, ...) end
  function o:GetNumberOption(k, d) return py.nopt(h, k, d or 0) end
  function o:GetName() return py.name(h) end
  function o:Enable() py.enable(h, true) end
  function o:Disable() py.enable(h, false) end
  return o
end
local function wrap_meter(h)
  if h == nil then return nil end
  local o = {}
  function o:GetOption(k, d) return py.opt(h, k, d or '') end
  function o:SetOption(...) each_pair(function(k, v) py.setoption(h, k, v, false) end, ...) end
  function o:GetNumberOption(k, d) return py.nopt(h, k, d or 0) end
  function o:GetName() return py.name(h) end
  function o:SetText(s) py.settext(h, wstr(s)) end
  function o:GetX() return py.geom(h, 'x') end
  function o:GetY() return py.geom(h, 'y') end
  function o:GetW() return py.geom(h, 'w') end
  function o:GetH() return py.geom(h, 'h') end
  function o:SetX(v) py.setgeom(h, 'x', v) end
  function o:SetY(v) py.setgeom(h, 'y', v) end
  function o:SetW(v) py.setgeom(h, 'w', v) end
  function o:SetH(v) py.setgeom(h, 'h', v) end
  function o:Show() py.hidden(h, false) end
  function o:Hide() py.hidden(h, true) end
  return o
end
-- Windows Lua reads files in text mode, which drops the \r of CRLF line ends; skins rely on it.
-- Paths are as Windows takes them: backslashes and any letter case (py.fspath).
do
  local real_open, real_lines, real_loadfile, real_dofile = io.open, io.lines, loadfile, dofile
  local function fs(path)
    if type(path) == 'string' then return py.fspath(path) end
    return path
  end
  local function text_file(content)
    content = content:gsub('\r\n', '\n')
    local pos, f = 1, {}
    local function read_one(fmt)
      if type(fmt) == 'number' then
        if pos > #content then return nil end
        local s = content:sub(pos, pos + fmt - 1)
        pos = pos + fmt
        return s
      end
      local c = tostring(fmt or 'l'):gsub('^%*', ''):sub(1, 1)
      if c == 'a' then
        local s = content:sub(pos)
        pos = #content + 1
        return s
      end
      if c == 'n' then
        local s, e = content:find('^%s*[-+]?%d*%.?%d+', pos)
        if not s then return nil end
        pos = e + 1
        return tonumber(content:sub(s, e))
      end
      if pos > #content then return nil end
      local e = content:find('\n', pos, true)
      local line
      if e then
        line = content:sub(pos, c == 'L' and e or e - 1)
        pos = e + 1
      else
        line = content:sub(pos)
        pos = #content + 1
      end
      return line
    end
    function f:read(...)
      local n = select('#', ...)
      if n == 0 then return read_one('l') end
      local out = {}
      for i = 1, n do out[i] = read_one((select(i, ...))) end
      return unpack(out, 1, n)
    end
    function f:lines() return function() return read_one('l') end end
    function f:seek(whence, offset)
      local base = ({set = 0, cur = pos - 1, ['end'] = #content})[whence or 'cur'] or (pos - 1)
      pos = base + (offset or 0) + 1
      return pos - 1
    end
    function f:close() return true end
    return f
  end
  io.open = function(path, mode)
    mode = mode or 'r'
    path = fs(path)
    if mode:find('[wa+b]') then return real_open(path, mode) end
    local h, err = real_open(path, 'rb')
    if not h then return nil, err end
    local data = h:read('*a')
    h:close()
    return text_file(data or '')
  end
  io.lines = function(path)
    if path == nil then return real_lines() end
    local f, err = io.open(path, 'r')
    if not f then error(err, 2) end
    return f:lines()
  end
  loadfile = function(path) return real_loadfile(fs(path)) end
  -- Rainmeter's dofile: UTF-8 BOM / UTF-16 files load; a missing or broken file is logged, not raised.
  dofile = function(path)
    if path == nil then return real_dofile() end
    local src, name = py.source(path)
    if src == nil then return end
    local chunk, err = (loadstring or load)(src, name)
    if not chunk then py.warn(err) return end
    return chunk()
  end
  -- require's Lua file searcher, with package.path templates such as #@#Scripts\?.lua.
  local searchers = package.loaders or package.searchers
  searchers[2] = function(name)
    local fname = name:gsub('%.', '/')
    local tried = {}
    for tpl in package.path:gmatch('[^;]+') do
      local file = fs((tpl:gsub('%?', function() return fname end)))
      local h = real_open(file, 'r')
      if h then
        h:close()
        local chunk, err = real_loadfile(file)
        if not chunk then error("error loading module '" .. name .. "' from file '" .. file .. "':\n\t" .. err, 0) end
        return chunk
      end
      tried[#tried + 1] = "\n\tno file '" .. file .. "'"
    end
    return table.concat(tried)
  end
end
SKIN = {}
function SKIN:Bang(...) py.bang(...) end
function SKIN:GetMeasure(n) return wrap_measure(py.measure(n)) end
function SKIN:GetMeter(n) return wrap_meter(py.meter(n)) end
function SKIN:SetOption(s, ...) s = wstr(s) each_pair(function(k, v) py.setoption(s, k, v, false) end, ...) end
function SKIN:SetOptionGroup(g, ...) g = wstr(g) each_pair(function(k, v) py.setoption(g, k, v, true) end, ...) end
function SKIN:GetVariable(n, d) local v = py.var(n) if v == nil then return d end return v end
function SKIN:SetVariable(...) each_pair(py.setvariable, ...) end
function SKIN:ReplaceVariables(s) return py.replace(s) end
function SKIN:ParseFormula(s) return py.formula(s) end
function SKIN:MoveWindow(x, y) py.movewindow(tonumber(x) or 0, tonumber(y) or 0) end
function SKIN:FadeWindow(from, to) py.fadewindow(tonumber(from) or 0, tonumber(to) or 0) end
function SKIN:MakePathAbsolute(p) return py.abspath(p) end
function SKIN:ReadTextFile(p) return py.readtext(wstr(p)) end
function SKIN:WriteTextFile(p, text, enc) return py.writetext(wstr(p), wstr(text), isstr(enc) and tostring(enc) or nil) end
function SKIN:GetX() return py.skingeom('x') end
function SKIN:GetY() return py.skingeom('y') end
function SKIN:GetW() return py.skingeom('w') end
function SKIN:GetH() return py.skingeom('h') end
SELF = wrap_measure(py.self())
-- Old scripts cast what SKIN:GetMeasure returns: tolua.cast(m, 'CMeasure'); the value is already usable.
tolua = {cast = function(x) return x end}
package.loaded.tolua = tolua
print = function(...) py.log(...) end
__raintux_deadline = math.huge
debug.sethook(function()
  if os.clock() > __raintux_deadline then
    __raintux_deadline = math.huge
    error("script took longer than 5 seconds; stopped", 2)
  end
end, "", 100000)
"""


class _Api:
    """Callbacks reachable from Lua; handles are section names."""

    def __init__(self, measure: ScriptMeasure) -> None:
        self.m = measure
        self.skin = measure.skin

    def self(self) -> str:
        return self.m.name

    def measure(self, name: Any) -> str | None:
        ms = self.skin.measure(str(name))
        return ms.name if ms is not None else None

    def meter(self, name: Any) -> str | None:
        mt = self.skin.meter(str(name))
        return mt.name if mt is not None else None

    def _sec(self, h: Any) -> Any:
        return self.skin.section(str(h))

    def mval(self, h: Any) -> float:
        return float(getattr(self.skin.measure(str(h)), "value", 0.0))

    def mstr(self, h: Any) -> str:
        ms = self.skin.measure(str(h))
        return ms.string_value() if ms is not None else ""

    def mrel(self, h: Any) -> float:
        ms = self.skin.measure(str(h))
        return ms.relative() if ms is not None else 0.0

    def mmin(self, h: Any) -> float:
        return float(getattr(self.skin.measure(str(h)), "min_value", 0.0))

    def mmax(self, h: Any) -> float:
        return float(getattr(self.skin.measure(str(h)), "max_value", 1.0))

    def opt(self, h: Any, key: Any, default: Any) -> str:
        sec = self._sec(h)
        if sec is None:
            return _lua_str(default)
        raw = sec.option_raw(str(key))
        return self.skin.resolve(raw, section_vars=True) if raw is not None else _lua_str(default)

    def nopt(self, h: Any, key: Any, default: Any) -> float:
        return parse_number(self.opt(h, key, ""), float(default or 0), self.skin.measure_number)

    def name(self, h: Any) -> str:
        return str(h)

    def enable(self, h: Any, on: bool) -> None:
        ms = self.skin.measure(str(h))
        if ms is not None:
            ms.disabled = not on

    def geom(self, h: Any, which: str) -> float:
        mt = self.skin.meter(str(h))
        if mt is None:
            return 0.0
        x, y, w, hh = mt.box()
        return {"x": x, "y": y, "w": w, "h": hh}[which]

    def setgeom(self, h: Any, which: str, v: Any) -> None:
        mt = self.skin.meter(str(h))
        if mt is not None:
            mt.set_option(which, trim_number(float(v)))
            mt.update(force=True)

    def hidden(self, h: Any, on: bool) -> None:
        mt = self.skin.meter(str(h))
        if mt is not None:
            mt.hidden = on

    def settext(self, h: Any, text: str) -> None:
        """meter:SetText: String and TextEdit meters only; anything else ignores it."""
        set_text = getattr(self.skin.meter(str(h)), "set_text", None)
        if set_text is not None:
            set_text(text)

    def setoption(self, section: str, key: str, value: str, group: bool) -> None:
        """Skin::SetOption: the value is stored raw (its variables resolve when the section re-reads,
        on its next update) and nothing updates now. ``Group=Name`` targets a group, as in Rainmeter."""
        if section.startswith("Group="):
            section, group = section[6:], True
        self.skin.bang("setoptiongroup" if group else "setoption", [section, key, value])

    def bang(self, *args: Any) -> None:
        parts = [_lua_str(a) for a in args]
        if not parts:
            return
        # source=None: #Variables# in bangs from Lua resolve with their current values.
        if len(parts) == 1:
            self.skin.execute(parts[0])
            return
        quoted = " ".join(p if (" " not in p and p) else '"' + p + '"' for p in parts[1:])
        self.skin.execute(f"[{parts[0]} {quoted}]")

    def var(self, name: Any) -> str | None:
        return self.skin.get_variable(str(name))

    def setvariable(self, name: str, value: str) -> None:
        """Skin::SetVariable, like !SetVariable: a value in parentheses is evaluated first."""
        self.skin.bang("setvariable", [name, value])

    def movewindow(self, x: Any, y: Any) -> None:
        self.skin.bang("move", [str(_lua_int(x)), str(_lua_int(y))])

    def fadewindow(self, start: Any, end: Any) -> None:
        # Not a bang: Rainmeter has no !FadeWindow, so the name stays out of HOST_BANGS.
        self.skin.host.host_bang(self.skin, "fadewindow", [str(_lua_int(start)), str(_lua_int(end))])

    def replace(self, s: Any) -> str:
        return self.skin.resolve(_lua_str(s), section_vars=True)

    def formula(self, s: Any) -> float | None:
        try:
            return evaluate(self.skin.resolve(_lua_str(s), section_vars=True), self.skin.measure_number)
        except FormulaError:
            return None

    def abspath(self, p: Any) -> str:
        return str(self.skin.path(str(p)))

    def readtext(self, p: str) -> str | tuple[None, str]:
        """SKIN:ReadTextFile: the text (BOM-detected encoding, CRLF read as LF), or nil and a message."""
        path = self.skin.path(p)
        try:
            raw = path.read_bytes()
        except OSError:
            return None, f"Unable to read file: {path}"
        return raw.decode(detect_encoding(raw), errors="replace").replace("\r\n", "\n")

    def writetext(self, p: str, text: str, encoding: str | None) -> bool | tuple[bool, str]:
        """SKIN:WriteTextFile: UTF-8 with BOM (or ANSI), CRLF line ends; true, or false and a message.

        Rainmeter accepts "UTF-16" but writes UTF-8 for it all the same; so does this.
        """
        codec = "utf-8-sig"
        if encoding is not None:
            name = encoding.lower()
            if name == "ansi":
                codec = "cp1252"
            elif name not in ("utf-8", "utf-16"):
                return False, f"Unknown encoding: {encoding}"
        path = self.skin.path(p)
        data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode(codec, errors="replace")
        try:
            path.write_bytes(data)
        except OSError:
            return False, f"Unable to write file: {path}"
        return True

    def skingeom(self, which: str) -> float:
        st = self.skin.state
        return float({"x": st.x, "y": st.y, "w": self.skin.width, "h": self.skin.height}[which])

    def log(self, *args: Any) -> None:
        log.info("[%s] %s", self.m.name, " ".join(_lua_str(a) for a in args))

    def warn(self, *args: Any) -> None:
        log.error("[%s] %s", self.m.name, " ".join(_lua_str(a) for a in args))

    def fspath(self, p: Any) -> str:
        """A path from Lua file functions as Windows takes it: backslashes, a drive, any letter case."""
        raw = _lua_str(p)
        if not raw or os.path.exists(raw):
            return raw
        path = Path(windows_to_posix(raw))
        return str(resolve_ci(path if path.is_absolute() else Path.cwd() / path))

    def source(self, p: Any) -> tuple[str, str] | None:
        """``dofile``: the script's text (UTF-8, maybe with BOM, or UTF-16) and its chunk name."""
        path = Path(self.fspath(p))
        try:
            raw = path.read_bytes()
        except OSError as e:
            log.warning("[%s] dofile: cannot read %s: %s", self.m.name, path, e)
            return None
        return raw.decode(detect_encoding(raw), errors="replace"), "@" + path.name


class ScriptMeasure(Measure):
    def read_measure(self) -> None:
        self.script_file = self.get("scriptfile")
        if not hasattr(self, "_lua"):
            self._lua: Any = None
            self._failed = False

    def initialize(self) -> None:
        if not self.script_file:
            log.warning("[%s] Script measure without ScriptFile", self.name)
            self._failed = True
            return
        if self.script_file.strip().strip('"').lower().endswith(".py"):
            log.warning("[%s] %s is a Python script; Python skin scripting is not available yet", self.name, self.script_file)
            self._failed = True
            return
        try:
            try:
                from lupa.lua51 import LuaRuntime  # Rainmeter embeds Lua 5.1 (loadstring, unpack, setfenv…)
            except ImportError:
                from lupa import LuaRuntime
        except ImportError:
            log.warning("[%s] Lua scripts need the 'lupa' package (pip install lupa)", self.name)
            self._failed = True
            return
        path = self.skin.path(self.script_file)
        try:
            raw = Path(path).read_bytes()
            source = raw.decode(detect_encoding(raw), errors="replace")  # Rainmeter accepts UTF-16 scripts
        except OSError as e:
            log.warning("[%s] cannot read %s: %s", self.name, path, e)
            self._failed = True
            return
        lua = LuaRuntime(unpack_returned_tuples=True)
        api = _Api(self)
        py = lua.table_from({k: getattr(api, k) for k in dir(api) if not k.startswith("_") and callable(getattr(api, k))})
        lua.execute(_PRELUDE, py)
        try:
            self._lua = lua
            self._arm()
            lua.execute(source)
            init = lua.globals().Initialize
            if init is not None:
                init()
        except Exception as e:
            log.error("[%s] Lua error in %s: %s", self.name, path.name, e)
        self._lua = lua

    def _arm(self) -> None:
        """Watchdog: a call that runs for more than 5 s of CPU raises a Lua error."""
        g = self._lua.globals()
        g["__raintux_deadline"] = self._lua.eval("os.clock()") + 5

    def update_value(self) -> None:
        if self._lua is None:
            return
        fn = self._lua.globals().Update
        if fn is None:
            return
        try:
            self._arm()
            result = fn()
        except Exception as e:
            log.error("[%s] Lua Update(): %s", self.name, e)
            return
        if isinstance(result, bool):
            result = 1 if result else 0
        if isinstance(result, (int, float)):
            self._raw_number = float(result)
            self.string = None
        elif result is not None:
            self.string = str(result)
            self._raw_number = parse_number(self.string, 0.0)

    def command(self, args: str) -> None:
        if self._lua is None:
            return
        try:
            self._arm()
            self._lua.execute(args)
        except Exception as e:
            log.error("[%s] Lua command %r: %s", self.name, args, e)

    def inline_call(self, call: str) -> Any:
        """``[&Measure:func('x')]``: evaluate a Lua expression and return its value."""
        if self._lua is None:
            return ""
        self._arm()  # every entry point restarts the 5 s watchdog
        result = self._lua.eval(call)
        return "" if result is None else _lua_str(result)
