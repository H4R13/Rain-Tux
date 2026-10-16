# RainTux

Rainmeter-style transparent desktop overlays for Linux, targeting **Wayland** compositors. The stack combines a Python core, GTK4 + Cairo rendering, and (where available) **wlr-layer-shell** / **gtk4-layer-shell** for desktop-layer surfaces.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or `pip`
- GTK 4, GObject Introspection, Cairo, Pango (system packages)
- Wayland session

### System packages (names vary by distro)

**Arch / Fedora / openSUSE**

- `gtk4`, `python-gobject`, `cairo`, `pango`, `gdk-pixbuf2`
- **Layer shell (Phase 1 default):** `gtk4-layer-shell` (provides GObject bindings for layered GTK4 windows on wlroots and KDE KWin)

**Debian / Ubuntu**

- `libgtk-4-1`, `gir1.2-gtk-4.0`, `libcairo2-dev`, `python3-gi`, `python3-gi-cairo`
- `libgtk4-layer-shell0` and typelibs if packaged (e.g. `gir1.2-gtk4layershell-1.0` when available), or build [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) from source (Ubuntu 24.04 does not package it; v1.0.4 builds fine with `meson setup build --prefix=/usr -Dintrospection=true`).
- To `pip install` (pip compiles pycairo / PyGObject): `python3-dev`, `libcairo2-dev`, `libgirepository-2.0-dev`, `pkg-config`. Alternatively create the venv with `--system-site-packages` and use the distro's `python3-gi`.

**dbus** (optional)

- `pip install ".[dbus]"` for MPRIS and the future GNOME helper (Phase 2+); building `dbus-python` needs `libdbus-1-dev`, or use the distro's `python3-dbus`.

Install Python deps:

```bash
make install
# or
uv sync
```

## Running

```bash
make run
# or
python -m raintux
```

**First launch:** a **splash** (optional GIF) and **terms** dialog run once. The GIF plays **one full loop** (frame durations summed via Pillow), then the EULA. After you check *I agree* and **Continue**, `~/.config/raintux/first_run_ok` is created. Skip: `RAINTUX_SKIP_FIRST_RUN=1`.

**Splash path:** **`launch.env`** sets `RAINTUX_SPLASH_GIF=loading_bg.gif` (next to `README.md`). If that line is missing, RainTux still looks for **`loading_bg.gif` in the repo root**, then `raintux installer temp/loading_bg.gif`, then `raintux/data/assets/splash.gif`. Export `RAINTUX_SPLASH_GIF` to override `launch.env`. No GIF → placeholder splash for `RAINTUX_SPLASH_SECONDS` (default `2.5`).

Development (verbose logging, reload-friendly defaults):

```bash
make dev
```

## Compositor notes

### Hyprland / Sway / other wlroots compositors

Install **gtk4-layer-shell**. It must be loaded before libwayland, so RainTux re-launches itself once with `LD_PRELOAD` pointing at `libgtk4-layer-shell.so` when the library is found (opt out: `RAINTUX_NO_PRELOAD=1`). RainTux selects the WLR-style backend when layer shell globals are present or when `XDG_CURRENT_DESKTOP` indicates a wlroots-based session. Surfaces use the **BOTTOM** layer and transparent RGBA by default.

### KDE Plasma (Wayland)

KWin supports layer-shell for many widgets; use the same **gtk4-layer-shell** build. RainTux detects KDE via `XDG_CURRENT_DESKTOP` and may apply KDE-specific margin/anchor tweaks (see `raintux/wayland/kde_backend.py`).

### GNOME (Wayland)

Mutter does **not** expose wlr-layer-shell to arbitrary clients. Phase 1 still runs as a transparent GTK window; for true desktop layering, install the future **raintux-helper** GNOME Shell extension (Phase 4) or use the documented XWayland fallback (`x11_fallback.py`).

## Paths (XDG)

| Purpose | Path |
|--------|------|
| Skins | `~/RainTux/Skins/` |
| Plugins | `~/RainTux/Plugins/` |
| Config | `~/.config/raintux/config.toml` |
| Logs / cache | `~/.local/share/raintux/` |

On first run, minimal default config and example skin directories are created if missing.

## Skin Manager UI (`ui/`)

React + Vite app in **`ui/`**. Start the backend first, then:

```bash
cd ui && npm install && npm run dev
```

Defaults: API `http://127.0.0.1:7272` (`VITE_API_URL`), dev server **port 8080** (see `ui/vite.config.ts`). Details: `ui/README.md` and `docs/UI_BUILD_PROMPT.md`.

From the repo root on Unix-like shells: `make ui-dev`.

## API (Phase 1)

Local **FastAPI** server listens on `127.0.0.1:7272` (configurable in `config.toml`):

- `GET /skins` — installed skins
- `GET /skins/active` — loaded skins
- `POST /skins/{id}/load` | `/unload` | `/refresh`
- `GET|PUT /skins/{id}/config`
- `GET /system/status`
- `WS /ws` — live events (stub stream in Phase 1)

## Phase status

- **Phase 1:** Compositor detection, layered GTK window (gtk4-layer-shell when available), basic `.ini` parser, CPU/RAM/Time/Disk measures, String/Image/Shape/Bar meters, FastAPI skeleton.
- **Phase 2–4:** See project brief in the repository docs / source TODOs.

## License

MIT
