"""Runtime compositor detection for backend selection."""

from __future__ import annotations

import enum
import os
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class CompositorKind(enum.Enum):
    """High-level compositor family for backend routing."""

    WLR = "wlr"  # wlroots: Sway, Hyprland, etc.
    KDE = "kde"  # Plasma / KWin
    GNOME = "gnome"  # Mutter
    UNKNOWN = "unknown"


def _xdg_desktops() -> list[str]:
    cur = os.environ.get("XDG_CURRENT_DESKTOP", "")
    session = os.environ.get("XDG_SESSION_DESKTOP", "")
    return [p.strip() for part in (cur, session) for p in part.split(":") if p.strip()]


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland")


def detect_compositor() -> CompositorKind:
    """
    Detect the active compositor using environment heuristics.

    Checks ``$XDG_CURRENT_DESKTOP``, ``$GNOME_DESKTOP_SESSION_ID``,
    ``$DESKTOP_SESSION``, and presence of ``$WAYLAND_DISPLAY``.
    Registry glob probing for ``zwlr_layer_shell_v1`` can be added via pywayland
    for harder guarantees on UNKNOWN desktops.
    """
    if not _is_wayland():
        # X11 or SSH; still return UNKNOWN — x11 fallback may apply elsewhere.
        pass

    desktops = {d.lower() for d in _xdg_desktops()}
    if "gnome" in desktops or os.environ.get("GNOME_DESKTOP_SESSION_ID"):
        return CompositorKind.GNOME
    if any(x in desktops for x in ("kde", "plasma")):
        return CompositorKind.KDE
    session = (os.environ.get("DESKTOP_SESSION") or "").lower()
    if "gnome" in session:
        return CompositorKind.GNOME
    if session in ("sway", "hyprland", "niri", "river", "wayfire") or any(
        x in desktops for x in ("sway", "hyprland", "niri", "river", "wayfire", "wlroots")
    ):
        return CompositorKind.WLR

    # wlroots often sets XDG_SESSION_DESKTOP=sway etc.; if Wayland and not KDE/GNOME, assume WLR-capable.
    if _is_wayland() and "kde" not in desktops and "gnome" not in desktops:
        return CompositorKind.WLR

    return CompositorKind.UNKNOWN


def new_layer_surfaces_on_top() -> bool:
    """Whether a newly mapped layer-shell surface goes on top of its layer (most compositors) or
    to the bottom (Sway 1.9, measured); see :mod:`raintux.core.stacking`."""
    desktops = {d.lower() for d in _xdg_desktops()}
    return not (os.environ.get("SWAYSOCK") or "sway" in desktops
                or (os.environ.get("DESKTOP_SESSION") or "").lower() == "sway")


def registry_has_layer_shell(timeout_s: float = 1.0) -> bool:
    """
    Best-effort probe for ``zwlr_layer_shell_v1`` using ``wayland-info`` when installed.

    Full in-process enumeration via pywayland is compositor-specific; this helper
    avoids fragile low-level bindings in Phase 1.
    """
    if not os.environ.get("WAYLAND_DISPLAY"):
        return False
    tool = shutil.which("wayland-info")
    if not tool:
        return False
    try:
        proc = subprocess.run(
            [tool],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    blob = (proc.stdout or "") + (proc.stderr or "")
    return "zwlr_layer_shell_v1" in blob
