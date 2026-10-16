# Changelog

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
