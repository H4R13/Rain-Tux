#!/bin/bash
# Load real Rainmeter suites in RainTux on a headless Sway and report what goes wrong.
#
#   tools/suitecheck.sh [--corpus DIR] [--out DIR] [--only NAME...] [suitecheck.py options]
#
# Starts its own headless Sway (WLR_BACKENDS=headless, private XDG_RUNTIME_DIR), so nothing
# appears on the desktop you are using, then runs tools/suitecheck.py inside it. Each suite gets
# a fresh HOME and its own RainTux; see tools/suitecheck.py for the corpus manifest.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PY=${PYTHON:-python3}
RES=${SUITECHECK_RESOLUTION:-1920x1080}

RUN=$(mktemp -d /tmp/suitecheck-xdg.XXXXXX)
chmod 700 "$RUN"
unset WAYLAND_DISPLAY DISPLAY SWAYSOCK
export XDG_RUNTIME_DIR=$RUN
cat > "$RUN/sway.conf" <<EOF
output HEADLESS-1 resolution $RES
output * bg #2d4a6b solid_color
default_border none
EOF
WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 WLR_RENDERER=pixman \
  sway -c "$RUN/sway.conf" > "$RUN/sway.log" 2>&1 &
SWAY=$!
trap 'kill $SWAY 2>/dev/null; wait $SWAY 2>/dev/null; rm -rf "$RUN"' EXIT
for _ in $(seq 40); do
  SOCK=$(ls "$RUN" 2>/dev/null | grep -E '^wayland-[0-9]+$' | head -1)
  [ -n "$SOCK" ] && break
  sleep 0.25
done
if [ -z "${SOCK:-}" ]; then
  echo "headless sway did not start:"; tail -n 20 "$RUN/sway.log"; exit 1
fi
export WAYLAND_DISPLAY=$SOCK
export SWAYSOCK=$(ls "$RUN"/sway-ipc.* 2>/dev/null | head -1)
export XDG_CURRENT_DESKTOP=sway XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
"$PY" "$HERE/suitecheck.py" "$@"
