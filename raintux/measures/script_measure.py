"""``Measure=Script``: Lua scripts with Rainmeter's SKIN / SELF API (via ``lupa``).

Supported: ``Initialize()``, ``Update()`` (number or string result), ``!CommandMeasure``
with Lua code, inline ``[&Script:func()]`` section variables, ``print``, and
SKIN:Bang / GetMeasure / GetMeter / GetVariable / ReplaceVariables / ParseFormula /
MakePathAbsolute / GetX / GetY / GetW / GetH; measure objects (GetValue,
GetStringValue, GetRelativeValue, GetMinValue, GetMaxValue, GetValueRange, GetOption,
GetNumberOption, GetName, Enable, Disable) and meter objects (GetOption,
GetNumberOption, GetName, GetX/Y/W/H, SetX/Y/W/H, Show, Hide).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from raintux.core.formula import FormulaError, evaluate, parse_number
from raintux.measures.base_measure import Measure, trim_number

log = logging.getLogger(__name__)

# Lua side of the API: thin tables whose methods call back into Python (``py``).
_PRELUDE = r"""
local py = ...
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
  function o:GetNumberOption(k, d) return py.nopt(h, k, d or 0) end
  function o:GetName() return py.name(h) end
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
SKIN = {}
function SKIN:Bang(...) py.bang(...) end
function SKIN:GetMeasure(n) return wrap_measure(py.measure(n)) end
function SKIN:GetMeter(n) return wrap_meter(py.meter(n)) end
function SKIN:GetVariable(n, d) local v = py.var(n) if v == nil then return d end return v end
function SKIN:ReplaceVariables(s) return py.replace(s) end
function SKIN:ParseFormula(s) return py.formula(s) end
function SKIN:MakePathAbsolute(p) return py.abspath(p) end
function SKIN:GetX() return py.skingeom('x') end
function SKIN:GetY() return py.skingeom('y') end
function SKIN:GetW() return py.skingeom('w') end
function SKIN:GetH() return py.skingeom('h') end
SELF = wrap_measure(py.self())
print = function(...) py.log(...) end
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
            return str(default)
        raw = sec.option_raw(str(key))
        return self.skin.resolve(raw, section_vars=True) if raw is not None else str(default)

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

    def bang(self, *args: Any) -> None:
        parts = [str(a) for a in args]
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

    def replace(self, s: Any) -> str:
        return self.skin.resolve(str(s), section_vars=True)

    def formula(self, s: Any) -> float | None:
        try:
            return evaluate(self.skin.resolve(str(s), section_vars=True), self.skin.measure_number)
        except FormulaError:
            return None

    def abspath(self, p: Any) -> str:
        return str(self.skin.path(str(p)))

    def skingeom(self, which: str) -> float:
        st = self.skin.state
        return float({"x": st.x, "y": st.y, "w": self.skin.width, "h": self.skin.height}[which])

    def log(self, *args: Any) -> None:
        log.info("[%s] %s", self.m.name, " ".join(str(a) for a in args))


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
        try:
            from lupa import LuaRuntime
        except ImportError:
            log.warning("[%s] Lua scripts need the 'lupa' package (pip install lupa)", self.name)
            self._failed = True
            return
        path = self.skin.path(self.script_file)
        try:
            source = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            log.warning("[%s] cannot read %s: %s", self.name, path, e)
            self._failed = True
            return
        lua = LuaRuntime(unpack_returned_tuples=True)
        api = _Api(self)
        py = lua.table_from({k: getattr(api, k) for k in dir(api) if not k.startswith("_") and callable(getattr(api, k))})
        lua.execute(_PRELUDE, py)
        try:
            lua.execute(source)
            init = lua.globals().Initialize
            if init is not None:
                init()
        except Exception as e:
            log.error("[%s] Lua error in %s: %s", self.name, path.name, e)
        self._lua = lua

    def update_value(self) -> None:
        if self._lua is None:
            return
        fn = self._lua.globals().Update
        if fn is None:
            return
        try:
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
            self._lua.execute(args)
        except Exception as e:
            log.error("[%s] Lua command %r: %s", self.name, args, e)

    def inline_call(self, call: str) -> Any:
        """``[&Measure:func('x')]``: evaluate a Lua expression and return its value."""
        if self._lua is None:
            return ""
        result = self._lua.eval(call)
        if isinstance(result, float) and result.is_integer():
            return int(result)
        return "" if result is None else result
