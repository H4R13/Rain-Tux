#!/usr/bin/env bash
# RainTux installer — the Linux counterpart of Rainmeter's setup.exe.
#
#   curl -fsSL https://raw.githubusercontent.com/H4R13/Rain-Tux/main/install.sh | bash
#
# Options (also as env: RAINTUX_BRANCH, RAINTUX_REPO):
#   --branch NAME          git branch to install (default: main)
#   --yes                  don't ask before installing system packages
#   --no-system-packages   skip the distro package step (you install dependencies yourself)
#   --no-autostart         don't start RainTux at login
#   --uninstall            remove the app (your ~/RainTux skins and settings are kept)
set -euo pipefail

REPO="${RAINTUX_REPO:-https://github.com/H4R13/Rain-Tux.git}"
BRANCH="${RAINTUX_BRANCH:-main}"
ASSUME_YES=0
SYSTEM_PACKAGES=1
AUTOSTART=1
UNINSTALL=0

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA/raintux/app"
VENV="$DATA/raintux/venv"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/raintux"
DESKTOP_FILE="$DATA/applications/raintux.desktop"
ICON_FILE="$DATA/icons/hicolor/scalable/apps/raintux.svg"
MIME_FILE="$DATA/mime/packages/raintux.xml"
AUTOSTART_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/raintux.desktop"
FONT_DIR="$DATA/fonts/raintux-selawik"
SELAWIK_URL="https://github.com/microsoft/Selawik/releases/download/1.01/Selawik_Release.zip"

while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="$2"; shift ;;
    --yes|-y) ASSUME_YES=1 ;;
    --no-system-packages) SYSTEM_PACKAGES=0 ;;
    --no-autostart) AUTOSTART=0 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
  die "run this as your normal user (it uses sudo only for system packages)"
fi

if [ "$UNINSTALL" -eq 1 ]; then
  say "Removing RainTux (skins in ~/RainTux and settings in ~/.config/raintux are kept)"
  pkill -f "python.* -m raintux" 2>/dev/null || true
  rm -rf "$APP_DIR" "$VENV" "$LAUNCHER" "$DESKTOP_FILE" "$ICON_FILE" "$MIME_FILE" "$AUTOSTART_FILE" "$FONT_DIR"
  command -v update-mime-database >/dev/null && update-mime-database "$DATA/mime" >/dev/null 2>&1 || true
  command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
  say "Done."
  exit 0
fi

confirm() {
  [ "$ASSUME_YES" -eq 1 ] && return 0
  if [ ! -t 0 ] && [ ! -e /dev/tty ]; then return 0; fi
  printf '%s [Y/n] ' "$1"
  read -r reply </dev/tty || reply=y
  case "$reply" in n|N|no|No) return 1 ;; *) return 0 ;; esac
}

sudo_cmd() { if command -v sudo >/dev/null; then sudo "$@"; else die "sudo is needed to install: $*"; fi; }

# ---------------------------------------------------------------- system packages
. /etc/os-release 2>/dev/null || true
FAMILY=""
case " ${ID:-} ${ID_LIKE:-} " in
  *" arch "*|*" manjaro "*|*" endeavouros "*) FAMILY=arch ;;
  *" fedora "*|*" rhel "*|*" centos "*) FAMILY=fedora ;;
  *" opensuse"*|*" suse "*) FAMILY=suse ;;
  *" debian "*|*" ubuntu "*) FAMILY=debian ;;
esac

typelib_present() {
  for d in /usr/lib/girepository-1.0 /usr/lib64/girepository-1.0 /usr/lib/*/girepository-1.0 /usr/local/lib/girepository-1.0 /usr/local/lib/*/girepository-1.0; do
    [ -e "$d/$1.typelib" ] && return 0
  done
  return 1
}

build_layer_shell() {
  say "Building gtk4-layer-shell 1.0.4 from source (your distro does not package it)"
  local tmp; tmp="$(mktemp -d)"
  git clone -q --depth 1 --branch v1.0.4 https://github.com/wmww/gtk4-layer-shell.git "$tmp/gls"
  (cd "$tmp/gls" && meson setup build --prefix=/usr -Dexamples=false -Ddocs=false -Dtests=false \
      -Dintrospection=true -Dvapi=false >/dev/null && ninja -C build >/dev/null && sudo_cmd ninja -C build install >/dev/null)
  sudo_cmd ldconfig || true
  rm -rf "$tmp"
}

install_packages() {
  case "$FAMILY" in
    debian)
      local pkgs="git python3 python3-venv python3-pip python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 gir1.2-rsvg-2.0 gir1.2-pango-1.0 librsvg2-common fonts-noto-core pulseaudio-utils"
      say "Installing: $pkgs"
      sudo_cmd apt-get update -qq
      # shellcheck disable=SC2086
      sudo_cmd apt-get install -y -qq $pkgs
      if ! sudo_cmd apt-get install -y -qq gir1.2-gtk4layershell-1.0 libgtk4-layer-shell0 2>/dev/null; then
        sudo_cmd apt-get install -y -qq meson ninja-build libgtk-4-dev libwayland-dev wayland-protocols \
          gobject-introspection libgirepository1.0-dev 2>/dev/null \
          || sudo_cmd apt-get install -y -qq meson ninja-build libgtk-4-dev libwayland-dev wayland-protocols \
               gobject-introspection libgirepository-2.0-dev
        build_layer_shell
      fi ;;
    fedora)
      sudo_cmd dnf install -y git python3 python3-gobject python3-cairo gtk4 gtk4-layer-shell librsvg2 \
        google-noto-sans-fonts pulseaudio-utils ;;
    arch)
      sudo_cmd pacman -S --needed --noconfirm git python python-gobject python-cairo gtk4 gtk4-layer-shell \
        librsvg noto-fonts libpulse ;;
    suse)
      sudo_cmd zypper --non-interactive install git python3 python3-gobject python3-gobject-cairo python3-cairo \
        gtk4 gtk4-layer-shell typelib-1_0-Gtk-4_0 typelib-1_0-Gtk4LayerShell-1_0 typelib-1_0-Rsvg-2_0 \
        noto-sans-fonts pulseaudio-utils ;;
    *)
      warn "Unknown distribution; install yourself: git, Python 3.11+, PyGObject, pycairo, GTK 4, librsvg (gir), gtk4-layer-shell"
      return ;;
  esac
}

if [ "$SYSTEM_PACKAGES" -eq 1 ]; then
  if confirm "Install the system packages RainTux needs (GTK 4, gtk4-layer-shell, PyGObject…) with sudo?"; then
    install_packages
  else
    warn "Skipping system packages."
  fi
fi

command -v git >/dev/null || die "git is required"
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 is required"
"$PY" - <<'EOF' || die "Python 3.11 or newer is required"
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF
"$PY" -c "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk" 2>/dev/null \
  || die "GTK 4 Python bindings (PyGObject + Gtk 4.0 typelib) are missing"
typelib_present Gtk4LayerShell-1.0 \
  || warn "gtk4-layer-shell not found: skins will be normal windows instead of desktop widgets"

# ---------------------------------------------------------------- application
mkdir -p "$(dirname "$APP_DIR")"
if [ -d "$APP_DIR/.git" ]; then
  say "Updating RainTux in $APP_DIR"
  git -C "$APP_DIR" remote set-url origin "$REPO"
  git -C "$APP_DIR" fetch -q origin "$BRANCH"
  git -C "$APP_DIR" checkout -q "$BRANCH" 2>/dev/null || git -C "$APP_DIR" checkout -q -b "$BRANCH" "origin/$BRANCH"
  if ! git -C "$APP_DIR" pull -q --ff-only origin "$BRANCH" 2>/dev/null; then
    # The branch was rewritten upstream; this directory is managed by the installer.
    warn "The $BRANCH branch was rewritten upstream; resetting $APP_DIR to it"
    git -C "$APP_DIR" checkout -q -f -B "$BRANCH" "origin/$BRANCH"
  fi
else
  say "Downloading RainTux ($BRANCH) to $APP_DIR"
  git clone -q --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

say "Setting up the Python environment"
[ -x "$VENV/bin/python" ] || "$PY" -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip
# PyGObject and pycairo come from the system packages above; everything else from PyPI.
"$VENV/bin/python" -m pip install -q "fastapi>=0.110" "uvicorn>=0.27" "pydantic>=2.6" "aiohttp>=3.9" \
  "Pillow>=10.2" "watchdog>=4" "rich>=13.7" "psutil>=5.9" "lupa>=2.0"
"$VENV/bin/python" -m pip install -q "numpy>=1.24" || warn "numpy not installed: AudioLevel bands and ColorMatrix are disabled"
"$VENV/bin/python" -m pip install -q --no-deps -e "$APP_DIR"

say "Installing the launcher, menu entry and .rmskin file association"
mkdir -p "$BIN_DIR" "$(dirname "$DESKTOP_FILE")" "$(dirname "$ICON_FILE")" "$(dirname "$MIME_FILE")"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
exec "$VENV/bin/python" -m raintux "\$@"
EOF
chmod +x "$LAUNCHER"
cp "$APP_DIR/raintux/data/icons/raintux.svg" "$ICON_FILE"
cat > "$MIME_FILE" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-rainmeter-skin">
    <comment>Rainmeter skin package</comment>
    <sub-class-of type="application/zip"/>
    <glob pattern="*.rmskin"/>
  </mime-type>
</mime-info>
EOF
desktop_entry() {
  cat <<EOF
[Desktop Entry]
Type=Application
Name=RainTux
GenericName=Desktop widgets
Comment=Rainmeter-compatible desktop skins for Linux
Exec=$LAUNCHER %f
Icon=raintux
Terminal=false
Categories=Utility;GTK;
MimeType=application/x-rainmeter-skin;
StartupNotify=false
EOF
}
desktop_entry > "$DESKTOP_FILE"
command -v update-mime-database >/dev/null && update-mime-database "$DATA/mime" >/dev/null 2>&1 || true
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
command -v xdg-mime >/dev/null && xdg-mime default raintux.desktop application/x-rainmeter-skin 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true

# Selawik is Microsoft's open-source (OFL) font with Segoe UI's metrics. Windows skins are laid
# out for Segoe UI, so with it text wraps and clips as it does on Windows.
if ! fc-list 2>/dev/null | grep -qi selawik; then
  say "Installing the Selawik font (Segoe UI metrics)"
  tmp="$(mktemp -d)"
  if command -v curl >/dev/null && curl -fsSL -o "$tmp/selawik.zip" "$SELAWIK_URL"; then
    mkdir -p "$FONT_DIR"
    "$PY" - "$tmp/selawik.zip" "$FONT_DIR" <<'EOF' || warn "Could not unpack Selawik"
import os, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    for name in z.namelist():
        if name.lower().endswith(".ttf"):
            with open(os.path.join(sys.argv[2], os.path.basename(name)), "wb") as out:
                out.write(z.read(name))
EOF
    command -v fc-cache >/dev/null && fc-cache -f "$FONT_DIR" >/dev/null 2>&1 || true
  else
    warn "Could not download Selawik; skins made for Segoe UI will use a wider font"
  fi
  rm -rf "$tmp"
fi

if [ "$AUTOSTART" -eq 1 ]; then
  mkdir -p "$(dirname "$AUTOSTART_FILE")"
  { desktop_entry; echo "X-GNOME-Autostart-enabled=true"; echo "X-GNOME-Autostart-Delay=3"; } > "$AUTOSTART_FILE"
fi

case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not on your PATH; start RainTux from the app menu or with $LAUNCHER" ;; esac

say "RainTux is installed."
echo "   Start it:            raintux        (or RainTux in your app menu)"
echo "   Install a skin:      raintux some-skin.rmskin   (or double-click the file)"
echo "   Run a bang:          raintux '!Refresh' 'illustro\\Clock'"
echo "   Update later:        rerun this script, or Manage → Settings → Update from GitHub"
echo "   Uninstall:           $APP_DIR/install.sh --uninstall"
case "${XDG_CURRENT_DESKTOP:-}" in
  *GNOME*) warn "GNOME does not offer desktop layers to apps: skins show as normal windows, and the tray icon needs the AppIndicator extension." ;;
esac
