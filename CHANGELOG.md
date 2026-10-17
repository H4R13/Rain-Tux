# Changelog

## Unreleased — license, bundled illustro, developer docs

- License: RainTux is now under the GNU GPL, version 2 or later, Rainmeter's license family.
  NOTICE and the README credit the Rainmeter team, whose skin format and source RainTux follows.
- illustro (RainTux edition) ships with RainTux and is installed on first launch: Linux mount
  points instead of drive letters, the desktop's system monitor instead of taskmgr.exe, "Trash"
  instead of "Recycle Bin" (a right-click no longer empties it without asking), and RainTux
  texts and links. It stays under CC BY-NC-SA 3.0 with credit to poiru.
- Skins with Python scripts (`ScriptFile=*.py`) install into `~/RainTux/PythonSkins`, apart from
  Rainmeter-format skins in `~/RainTux/Skins`. Until Python scripting lands, such a measure
  says so instead of failing as a Lua script.
- docs/ARCHITECTURE.md and CONTRIBUTING.md explain the code layout, the life of a skin, and
  where to add measures, meters, plugins and bangs.

## Unreleased — tested with real skin packages

All 211 skins in Sonder, SysDash, ModernGadgets, Win10 Widgets, Monstercat Visualizer, Fountain
of Colors and DetailedSystemMonitor now install, load and render; each fix below follows
Rainmeter's source.

- Lua runs on Lua 5.1 like Rainmeter (`loadstring`, `unpack`, number formatting); UTF-16 scripts
  load; text-mode file reads drop the `
` of CRLF lines as on Windows; inline `[&Script:…]`
  calls restart the 5 s watchdog.
- Option reads: `[Section]` variables resolve on every read; `[Meter:X/Y]` follows the relative
  chain before the first layout; formulas are anything starting with `(`; `%APPDATA%`-style
  Windows variables expand; `#CURRENTSECTION#`, `#PROGRAMDRIVE#` and screen variables exist;
  built-in variables work in `@Include` paths.
- Bangs: arguments are split before section variables resolve (values with spaces stay whole);
  `!SetOption` with an empty value restores the MeterStyle; `OnChangeAction` can't loop through
  `!Update`; If/Update/Change actions run in Rainmeter's order.
- Measures: formatted Time measures take their number from the text; `TimeStampFormat` accepts
  `%D`/`%T`/`%F`…; String measures have their value before the first update; FreeDiskSpace
  `Type=1` returns Rainmeter's numbers.
- Shapes: Combine uses the base shape's fill and its own transforms (`Shape1` = `Shape`); Arc
  takes radius/angle/flags in Rainmeter's order, open arcs/curves aren't filled; a partial
  `TransformOrder` keeps the other steps; `*` keeps an ArcTo default.
- Text: AccurateText=0 keeps the full W for clipping; InlineSetting Shadow keeps inline sizes;
  InlineSetting parameters accept formulas; fonts bundled with a skin show even when registered
  after the first render; PCRE classes like `[:alpha:]` work.
- Speed: the `#Variable#` table is cached per skin (an 800-meter Sonder skin loads in about 1 s
  instead of minutes); meters with runaway coordinates no longer stretch the window.
- Desktop (tested live in Sway with a virtual pointer and keyboard): clicks, hover and drags work
  on PyGObject < 3.50 (Ubuntu 24.04), dragging on Sway keeps the skin under the pointer, Image
  meters take clicks over their whole rectangle as in Rainmeter, a Roundline without a measure
  is drawn full.
- `install.sh` installs Selawik (Segoe UI's metrics, OFL) so Windows skins wrap text as on
  Windows; re-running it follows `RAINTUX_REPO` and survives a rewritten branch.

## 0.9.0 — Rainmeter parity

The engine was rebuilt around Rainmeter's model so that Windows skins run unchanged.

- New skin runtime: one ordered update cycle per skin, Rainmeter formulas, tolerant INI reader
  (UTF-16, in-place `@Include`), section variables (all forms, incl. `[&…]`, `[#…]`, `[$…]`),
  bangs (full set + namespaced section bangs + `!Delay`), mouse actions and mouse variables.
- Sizing and positioning follow Rainmeter's source (hidden meters, `R` positioning,
  BackgroundMargins), so suites like illustro look identical.
- Meters: String, Image, Bar, Shape, Roundline, Rotator, Histogram, Line, Button, Bitmap,
  TextEdit, Svg.
- Measures: system measures, Calc/String/Loop/Time/Uptime, Lua scripts, WebParser, ActionTimer,
  NowPlaying (MPRIS), audio, battery, SysInfo, Process, RunCommand, Ping, UsageMonitor,
  WifiStatus, temperatures, FolderInfo, FileView, Quote, RecycleManager, InputText, Mouse,
  DragDrop, MediaKey.
- Desktop: layer-shell windows with z-order, click-through, dragging/snapping, fades, zoom;
  right-click menu; tray icon; Manage window (Skins / Layouts / Settings / Log); layouts;
  `.rmskin` installer with backups and VariableFiles; skin fonts; single-instance command line
  (`raintux '!Bang' …`).
- `install.sh`: installs dependencies, RainTux from GitHub, menu entry, `.rmskin` association,
  autostart; also updates and uninstalls.
- New default skin suite "RainTux" (Welcome, Clock, System, Network).

## 0.1.x

- Initial prototype and review fixes (startup, layer-shell anchoring, packaging, CORS, path
  traversal, threading).
