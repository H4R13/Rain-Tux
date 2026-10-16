"""Desktop integration: start at login, app launcher entry, updating a git checkout."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def autostart_file() -> Path:
    return _config_home() / "autostart" / "raintux.desktop"


def launch_command() -> str:
    """Command that starts this installation (works for venv and system installs)."""
    return f"{shlex.quote(sys.executable)} -m raintux"


def desktop_entry(autostart: bool = False) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=RainTux",
        "GenericName=Desktop widgets",
        "Comment=Rainmeter-compatible desktop skins for Linux",
        f"Exec={launch_command()} %f",
        "Icon=raintux",
        "Terminal=false",
        "Categories=Utility;GTK;",
        "MimeType=application/x-rainmeter-skin;",
        "StartupNotify=false",
    ]
    if autostart:
        lines += ["X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-Delay=3"]
    return "\n".join(lines) + "\n"


def is_autostart() -> bool:
    return autostart_file().is_file()


def set_autostart(enabled: bool) -> None:
    f = autostart_file()
    if enabled:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(desktop_entry(autostart=True), encoding="utf-8")
    elif f.exists():
        f.unlink()


def repo_root() -> Path | None:
    root = Path(__file__).resolve().parent.parent.parent
    return root if (root / ".git").exists() else None


def update_checkout() -> tuple[bool, str]:
    """``git pull --ff-only`` in a git-based install (what install.sh sets up)."""
    root = repo_root()
    if root is None:
        return False, "This installation is not a git checkout; reinstall with install.sh to get updates."
    try:
        r = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0, out or "Up to date."
