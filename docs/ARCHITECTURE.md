# How RainTux is built

RainTux runs Rainmeter skins on Linux. This page is the map: what each part does, how a skin
goes from an `.ini` file to pixels on the desktop, and where to make the common kinds of change.

## The big picture

RainTux has two halves, joined by one small interface:

```
 ┌────────────────────────────── core (no GTK; tested headlessly) ───────────────────────────────┐
 │  skin_parser.py      reads the .ini: sections, @Include, [Variables]                          │
 │  skin.py  Skin       one loaded skin: variables, measures, meters, update cycle, bangs, mouse │
 │  measures/           data sources (CPU, Time, Calc, Script/Lua, plugins…)                     │
 │  meters/             drawing (String, Image, Shape, Bar…) onto a Cairo context                │
 └─────────────────────────────────────────┬─────────────────────────────────────────────────────┘
                                           │  SkinHost (skin.py): redraw, resize, timers,
                                           │  run programs, find other skins, desktop bangs
 ┌─────────────────────────────────────────┴──── desktop (GTK 4) ────────────────────────────────┐
 │  core/engine.py   RainTuxEngine: the app, loaded skins, their windows and update timers      │
 │  wayland/         one layer-shell window per skin: placement, input, dragging, opacity       │
 │  gui/             right-click menu, Manage window, install dialog, fonts, tray               │
 │  api/             local HTTP/WebSocket API (port 7272) for scripts and the web UI in ui/     │
 └───────────────────────────────────────────────────────────────────────────────────────────────┘
```

Everything a skin does to the desktop goes through `SkinHost`. In tests, `NullHost` stands in
for the desktop and simply records what the skin asked for, so the whole of Rainmeter's
behaviour can be tested without a screen.

## Where things live

| Path | What it is |
|------|------------|
| `raintux/__main__.py` | Command line: start the app, forward `raintux '!Bang' …` to a running instance, install `.rmskin` |
| `raintux/core/engine.py` | `RainTuxEngine`: finds skins, activates configs, runs each skin's update timer, desktop-wide bangs (`!Move`, `!ActivateConfig`, `!LoadLayout`…), running programs |
| `raintux/core/skin.py` | `Skin`: load, update cycle, layout, drawing order, all skin-local bangs (`_bang_*`), mouse input. Also `SkinHost` and `NullHost` |
| `raintux/core/skin_parser.py` | INI reading: sections, `@Include`, variables, `@Resources` lookup |
| `raintux/core/section.py` | `Section`: option lookup shared by measures and meters (`MeterStyle`, `!SetOption` overrides, `DynamicVariables`) |
| `raintux/core/formula.py` | Rainmeter formulas (`(1+2)`, functions, operators) |
| `raintux/core/bangs.py` | Splitting `[!Bang a "b c"][!Other]` into commands and arguments |
| `raintux/core/paths.py` | Windows paths on Linux: backslashes, drive letters, case-insensitive file names |
| `raintux/core/pcre.py` | Rainmeter's PCRE regex dialect translated to Python `re` |
| `raintux/core/position.py` | Window positions as Rainmeter reads them (`WindowX`, anchors, `R`/`B`/`%`, formulas) |
| `raintux/core/layouts.py`, `state.py` | Saved layouts, and which skins are loaded where (Rainmeter.ini's job) |
| `raintux/core/rmskin.py` | Reading and installing `.rmskin` packages |
| `raintux/core/config.py` | `config.toml` and the default folders (`~/RainTux/Skins`, `~/RainTux/PythonSkins`, `~/RainTux/Layouts`) |
| `raintux/measures/` | One file per measure type; `__init__.py` maps `Measure=` and `Plugin=` names to classes |
| `raintux/measures/plugins/` | Linux versions of Rainmeter plugins (WebParser, NowPlaying, AudioLevel, RecycleManager…) |
| `raintux/measures/script_measure.py` | `Measure=Script`: Lua 5.1 (via lupa) with Rainmeter's `SKIN`/`SELF` API |
| `raintux/meters/` | One file per meter type; `__init__.py` maps `Meter=` names to classes; `text.py` is Pango text layout |
| `raintux/wayland/layer_window.py` | `SkinWindow`: the GTK window for one skin |
| `raintux/gui/` | Manage window, context menu, installer, fonts, tray icon |
| `raintux/data/skins/` | Skins installed on first launch (RainTux suite, illustro RainTux edition) |
| `tests/` | pytest suite; `tests/helpers.py` `make_skin()` builds a skin on disk and loads it headlessly |

## The life of a skin

1. **Find.** `RainTuxEngine.list_configs()` scans every skins folder. A config is a folder holding
   `.ini` files (`illustro\Clock`); `@Resources` folders are skipped.
2. **Load.** `Skin.load()` parses the INI (`skin_parser.parse_skin_ini`), builds the variable
   table, and creates one object per section: `create_measure()` for `Measure=` sections and
   `create_meter()` for `Meter=` sections, in file order. Each section then `read()`s its options.
3. **Start.** `Skin.start()` runs the first update, then `OnRefreshAction`.
4. **Update cycle.** The engine's timer (`[Rainmeter] Update=`, 1000 ms by default) calls
   `Skin.update()`, which does what Rainmeter does on every tick:
   - every measure updates (`Measure.update()` → `update_value()`, then substitutes,
     `IfCondition`/`IfAbove`/`OnChangeAction`);
   - every meter updates (`Meter.update()` → `update_meter()`);
   - `layout()` places meters (relative `r`/`R` positions, containers) and sizes the window;
   - the host is asked to redraw, and `OnUpdateAction` runs.

   A section whose options changed (`!SetOption`, or `DynamicVariables=1`) re-reads them first.
5. **Draw.** The window calls `Skin.draw(cr)` with a Cairo context: the skin background, then
   every visible meter in file order (`Meter.draw()` → `draw_content()`).
6. **Input.** Pointer events go to `Skin.mouse()`, which finds the meter under the pointer and
   runs its mouse actions. Actions are bang strings, run by `Skin.execute()`.
7. **Bangs.** `Skin.execute("[!SetVariable X 1][!Redraw]")` splits the string and dispatches each
   bang. There are three kinds:
   - **skin bangs** act on the skin itself: `Skin._bang_<name>`, with argument counts in
     `_LOCAL_ARGC` (`!SetOption`, `!ShowMeter`, `!CommandMeasure`…);
   - **host bangs** act on windows or other skins: listed in `HOST_BANGS` and handled in
     `RainTuxEngine.host_bang` (`!Move`, `!ActivateConfig`, `!LoadLayout`…);
   - **section bangs** belong to a measure or meter type (`!ActionTimer:Execute 1`,
     `!TextEdit:Clear`): listed in `SECTION_BANGS` and handled by that class's `command()`.

## Making changes

### Add a measure

1. Create `raintux/measures/my_measure.py` with a subclass of `Measure`
   (`raintux/measures/base_measure.py`):
   - `read_measure()` reads options with `self.get()`, `self.get_number()`, `self.get_bool()`;
   - `update_value()` sets `self._raw_number` and/or `self.string`;
   - optional: `initialize()` (once, after the first read), `finalize()` (on unload),
     `command(args)` (for `!CommandMeasure`), `run_in_background(work, done)` for slow work.
2. Register it in `_BUILTIN` in `raintux/measures/__init__.py` (`"mytype": "my_measure:MyMeasure"`).
   For an old `Plugin=MyPlugin.dll`, add it to `_PLUGINS` as well.
3. Add a test with `make_skin()` (see `tests/test_plugins.py` for examples).

### Add a meter

Subclass `Meter` (`raintux/meters/base_meter.py`): read options in `read_meter()`, compute the
natural size in `update_meter()` (call `self.natural_size(w, h)`), and draw in
`draw_content(cr)` with Cairo, relative to the meter's own top-left corner. Register the class
in `_registry()` in `raintux/meters/__init__.py`.

### Add a bang

- Skin-local: add `def _bang_mybang(self, arg1: str, …)` to `Skin` and its argument count to
  `_LOCAL_ARGC` in `raintux/core/skin.py`.
- Desktop-wide: add the name to `HOST_BANGS` and a branch in `RainTuxEngine.host_bang`.
- For a measure or meter type: add `"type:action": argc` to `SECTION_BANGS`, give the class
  `bang_namespace = "type"`, and handle the action in its `command()`.

### Add to the Lua API

Lua scripts see `SKIN`, `SELF` and the measure/meter objects defined in the Lua prelude
`_PRELUDE` in `raintux/measures/script_measure.py`. Each method there is a thin wrapper that
calls a method of the Python class `_Api` in the same file. Add the Lua wrapper and the Python
method together, and check Rainmeter's own binding (`Library/LuaBinding*.cpp` in Rainmeter's
source) for argument order and return values.

### Rainmeter is the specification

When RainTux and Rainmeter disagree, Rainmeter is right. Rainmeter's source
(https://github.com/rainmeter/rainmeter, mostly `Library/`) is the reference: find the C++ that
handles the option or bang, match its behaviour, and say in the commit message which file you
followed. Tests named after the real skin that exposed a bug (`tests/test_real_skins.py`) keep
it fixed.

## Skins folders

| Folder | Holds |
|--------|-------|
| `~/RainTux/Skins/` | Rainmeter-format skins: `.ini` files, Lua scripts, `@Resources` |
| `~/RainTux/PythonSkins/` | Skins whose `Measure=Script` uses a Python file (`ScriptFile=*.py`). Python scripting itself is still to come; until then such a measure logs that it isn't available |
| `~/RainTux/Layouts/` | Saved layouts (`Rainmeter.ini` or `layout.json` per layout) |
| `raintux/data/skins/` | Bundled skins, copied into `~/RainTux/Skins/` when missing |

Config names are relative to their folder (`illustro\Clock`), so a skin works the same in either
folder. `rmskin.install_root()` decides where an installed package goes.
