# Rainmeter compatibility

RainTux aims to run Rainmeter skins unchanged. This page lists how each part maps to Linux.
"Same" means the behaviour follows Rainmeter's documented behaviour and, where it matters,
Rainmeter's source (sizing, relative positions, Show/Hide, section variables).

## Skin files

| Feature | Status |
|---------|--------|
| UTF-8 / UTF-16 (BOM) / ANSI files, CRLF | Same |
| `@Include`, `@IncludeN` (in place, nested, relative to the including file) | Same |
| `[Rainmeter]`, `[Variables]`, `[Metadata]`, styles | Same |
| `#Var#`, `#*Escape*#`, built-in variables incl. `@N` monitor variants | Same |
| Formulas `(…)` — operators, ternary, functions, hex/binary | Same (division by zero = 0) |
| Windows paths (`\`, drive letters, file-name case) | Converted; file names matched case-insensitively |
| Windows fonts (Segoe UI, Trebuchet MS, Calibri, Consolas…) | Mapped to close free fonts; `@Resources/Fonts` and `LocalFont` are loaded |

## Meters

| Meter | Notes |
|-------|-------|
| String | Pango text; StringAlign, StringStyle/FontWeight, StringCase, StringEffect, ClipString 0/1/2, InlineSetting (Color, Face, Size, Weight, Italic, Underline, Strikethrough, CharacterSpacing, Shadow, Case, Typography, GradientColor ≈ first stop), Angle, AutoScale/Scale/NumOfDecimals/Percentual, AccurateText=0 padding |
| Image | All scaling modes, ScaleMargins, Tile, ImageCrop (origins), ImageFlip/Rotate, ImageTint, ImageAlpha, Greyscale, ColorMatrix (needs numpy), MaskImageName; transparent pixels click through |
| Shape | Rectangle (rounded), Ellipse, Line, Arc, Curve, Path (LineTo/ArcTo/CurveTo/ClosePath), Combine (Union/Intersect/XOR/Exclude — fill only), Fill/Stroke Color/LinearGradient/RadialGradient, StrokeWidth/Dashes/Caps/Join, Rotate/Scale/Skew/Offset, TransformOrder, Extend |
| Bar, Roundline, Rotator, Histogram, Line, Button, Bitmap | Same options |
| TextEdit | Caret, selection, IME, clipboard, undo, InputFilter, Password, placeholder, `[$Input]`, `!TextEdit:*` |
| Svg | Needs librsvg's GObject bindings (`gir1.2-rsvg-2.0`); `Attribute` rewrites by `#id`, `.class`, tag |
| All meters | X/Y with `r`/`R`, W/H, Padding, SolidColor(2)/GradientAngle, BevelType, Hidden, MeterStyle, Container (alpha mask), TransformationMatrix, ToolTipText/Title, MouseActionCursor(Name, .cur files) |

## Measures

| Measure | Linux source |
|---------|--------------|
| CPU (Processor=N) | /proc via psutil, per-core |
| PhysicalMemory / SwapMemory / Memory | psutil (bytes, MaxValue = total) |
| FreeDiskSpace | `C:` = `/`, later letters = removable mounts, or `Drive=/path` |
| NetIn / NetOut / NetTotal | psutil per interface; auto MaxValue |
| Time, Uptime, Calc, String, Loop | Same |
| Script | Lua 5.4 via lupa; SKIN/SELF API; 5 s watchdog per call |
| WebParser | urllib in a worker thread; PCRE `(?siU)` translated; Download=1 to `~/.cache/raintux` |
| NowPlaying | MPRIS (any player); `PlayerName` picks one, else the playing one |
| Audio / Win7AudioPlugin | wpctl or pactl |
| AudioLevel | parec / pw-record monitor; bands and FFT with numpy |
| Power / PowerPlugin | psutil battery |
| SysInfo | hostname, user, OS, IP/gateway/DNS, screen sizes… |
| Process, Ping, RunCommand (`/bin/sh -c`), UsageMonitor/AdvancedCPU, WifiStatus (nmcli), CoreTemp/SpeedFan (sensors) | Linux equivalents |
| FolderInfo, FileView, Quote, RecycleManager (XDG trash) | Same |
| InputText | Inline text box over the skin |
| Mouse | Move/drag actions; `!Mouse:StartCapture` uses a full-screen overlay |
| DragDrop | Files dropped on the skin; Delete moves to the trash |
| Registry, PerfMon, ResMon, WindowMessage, Windows plugin DLLs | Not available (return 0 / empty, logged once) |

## Bangs

All skin bangs, group bangs and window bangs are implemented, including `!Delay`, namespaced
section bangs (`!ActionTimer:Execute`, `!WebParser:Refetch`, `!NowPlaying:TogglePlay`,
`!TextEdit:Focus`, `!Loop:Reset`, `!Mouse:StartCapture`, `!RecycleManager:EmptyBin`, …),
`!Skin:Load/Unload/Toggle/Edit` and `!Skin:Bang "Config"` / `Group=` targets.
No effect on Linux: blur bangs, `!AutoSelectScreen`, `!ResetStats`, `!LsBoxHook`.
`!SetWallpaper` works on GNOME, KDE, Sway and with swww/swaybg.

## Window behaviour

| Setting | Wayland (layer-shell) |
|---------|-----------------------|
| Position (WindowX/Y), multi-monitor | Margins on the right output |
| AlwaysOnTop -2…2 | background, bottom, bottom, top, overlay layers |
| Click through / transparent pixels | Input region |
| Draggable, Snap to edges, Keep on screen, Save position | Same |
| Transparency, fades, Hide on mouse over | Same |
| Zoom (`!SetZoomFactor`) | Same |
