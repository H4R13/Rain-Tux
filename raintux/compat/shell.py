"""Windows click targets in skin actions, translated to Linux.

Skins open things the Windows way: ``Explorer Shell:::{CLSID}``, ``shell:RecycleBinFolder``,
``ms-settings:``, ``shutdown.exe /s /t 0``, ``rundll32.exe powrprof.dll,SetSuspendState``, a
program by its ``.exe`` name or path, or a ``.lnk`` shortcut. :func:`translate` says what to
run instead: a URI to open, a command line, or the desktop's own app for a role ("@browser").
"""

from __future__ import annotations

import os
import re
import shutil
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Shell folders by CLSID and by shell: name -> a GIO URI or an XDG user folder ("xdg:NAME").
_FOLDERS = {
    "{20d04fe0-3aea-1069-a2d8-08002b30309d}": "computer:///",  # This PC
    "mycomputerfolder": "computer:///",
    "{645ff040-5081-101b-9f08-00aa002f954e}": "trash:///",  # Recycle Bin
    "recyclebinfolder": "trash:///",
    "{208d2c60-3aea-1069-a2d7-08002b30309d}": "network:///",
    "{f02c1a0d-be21-4350-88b0-7367fc96ef3c}": "network:///",
    "networkplacesfolder": "network:///",
    "{59031a47-3f72-44a7-89c5-5595fe6b30ee}": "xdg:HOME",  # the user's folder
    "profile": "xdg:HOME", "userprofiles": "xdg:HOME",
    "personal": "xdg:DOCUMENTS", "mydocuments": "xdg:DOCUMENTS", "documents": "xdg:DOCUMENTS",
    "downloads": "xdg:DOWNLOAD", "my music": "xdg:MUSIC", "mymusic": "xdg:MUSIC", "music": "xdg:MUSIC",
    "my pictures": "xdg:PICTURES", "mypictures": "xdg:PICTURES", "pictures": "xdg:PICTURES",
    "my video": "xdg:VIDEOS", "myvideo": "xdg:VIDEOS", "videos": "xdg:VIDEOS", "desktop": "xdg:DESKTOP",
    "{21ec2020-3aea-1069-a2dd-08002b30309d}": "@settings",  # Control Panel
    "{26ee0668-a00a-44d7-9371-beb064c98683}": "@settings",
    "controlpanelfolder": "@settings",
    "appsfolder": "@apps",
}

# Program (exe name without .exe) -> Linux commands to try in order; "@role" = the desktop's app.
_PROGRAMS: dict[str, tuple[str, ...]] = {
    "chrome": ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "@browser"),
    "chrome_proxy": ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "@browser"),
    "msedge": ("microsoft-edge", "microsoft-edge-stable", "@browser"),
    "firefox": ("firefox", "firefox-esr", "@browser"),
    "brave": ("brave-browser", "brave", "@browser"),
    "opera": ("opera", "@browser"), "iexplore": ("@browser",), "vivaldi": ("vivaldi", "@browser"),
    "notepad": ("@editor",), "notepad++": ("notepad-plus-plus", "@editor"), "wordpad": ("@editor",),
    "cmd": ("@terminal",), "powershell": ("@terminal",), "pwsh": ("@terminal",), "wt": ("@terminal",),
    "windowsterminal": ("@terminal",),
    "taskmgr": ("gnome-system-monitor", "plasma-systemmonitor", "ksysguard", "mate-system-monitor",
                "xfce4-taskmanager", "lxtask", "missioncenter"),
    "resmon": ("gnome-system-monitor", "plasma-systemmonitor", "ksysguard", "mate-system-monitor"),
    "perfmon": ("gnome-system-monitor", "plasma-systemmonitor", "ksysguard", "mate-system-monitor"),
    "calc": ("gnome-calculator", "kcalc", "qalculate-gtk", "galculator", "mate-calc"),
    "mspaint": ("kolourpaint", "pinta", "drawing", "gimp"),
    "control": ("@settings",), "systemsettings": ("@settings",),
    "winword": ("libreoffice --writer",), "excel": ("libreoffice --calc",),
    "powerpnt": ("libreoffice --impress",), "powerpoint": ("libreoffice --impress",),
    "outlook": ("thunderbird", "evolution", "kmail", "geary"), "thunderbird": ("thunderbird",),
    "obs64": ("obs",), "obs32": ("obs",), "ts3client_win32": ("teamspeak3", "ts3client"),
    "ts3client_win64": ("teamspeak3", "ts3client"), "utorrent": ("qbittorrent", "transmission-gtk", "deluge"),
    "minecraft": ("minecraft-launcher", "prismlauncher"), "minecraftlauncher": ("minecraft-launcher", "prismlauncher"),
    "telegram": ("telegram-desktop", "Telegram"), "discord": ("discord", "vesktop"),
    "code": ("code", "codium"), "localsend_app": ("localsend_app", "localsend"),
}

ROLES = {  # an app for a role, by the commands to try
    "@terminal": ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "ptyxis", "kgx", "foot",
                  "kitty", "alacritty", "wezterm", "xterm"),
    "@settings": ("gnome-control-center", "systemsettings", "xfce4-settings-manager", "cinnamon-settings",
                  "mate-control-center", "lxqt-config", "nwg-look"),
    "@editor": ("gnome-text-editor", "gedit", "kate", "kwrite", "mousepad", "xed", "pluma", "featherpad"),
}


@dataclass
class Action:
    """What to do for a Windows target: open ``uri``, or run ``argv`` (``role``: an "@..." app)."""

    uri: str = ""
    argv: list[str] = field(default_factory=list)
    role: str = ""
    note: str = ""  # why nothing can be done, when nothing can


def _xdg_dir(name: str) -> str:
    if name == "HOME":
        return str(Path.home())
    try:
        from gi.repository import GLib

        key = getattr(GLib.UserDirectory, f"DIRECTORY_{name}", None)
        found = GLib.get_user_special_dir(key) if key is not None else None
    except Exception:  # noqa: BLE001 - no GLib: fall back to the usual names
        found = None
    return found or str(Path.home() / name.capitalize())


def _folder(target: str) -> Action | None:
    t = target.strip().strip('"').lower()
    if t.startswith("shell:"):
        t = t[len("shell:"):].lstrip(":")
    elif t.startswith("::{"):
        t = t[2:]
    else:
        return None
    hit = _FOLDERS.get(t)
    if hit is None:
        return None
    if hit.startswith("xdg:"):
        return Action(uri=Path(_xdg_dir(hit[4:])).as_uri())
    if hit.startswith("@"):
        return Action(role=hit)
    return Action(uri=hit)


def _program(exe: str, args: list[str], which: Callable[[str], str | None]) -> Action | None:
    base = re.split(r"[\\/]", exe.strip().strip('"'))[-1].lower()
    name = base[:-4] if base.endswith(".exe") else base
    for cand in _PROGRAMS.get(name, ()) + ((name,) if base.endswith(".exe") else ()):
        if cand.startswith("@"):
            return Action(role=cand, argv=args)
        prog, *extra = cand.split()
        found = which(prog)
        if found:
            return Action(argv=[found, *extra, *args])
    if base.endswith(".exe"):
        return Action(note=f"{base} is a Windows program with no Linux counterpart here")
    return None


def _power(prog: str, args: list[str]) -> Action | None:
    low = [a.lower() for a in args]
    if prog == "shutdown":
        delay = int(low[low.index("/t") + 1]) if "/t" in low and low.index("/t") + 1 < len(low) and \
            low[low.index("/t") + 1].isdigit() else 0
        later = [f"+{max(1, round(delay / 60))}"] if delay else []
        if "/a" in low:
            return Action(argv=["shutdown", "-c"])
        if "/l" in low:
            return Action(argv=["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "self")])
        if "/h" in low:
            return Action(argv=["systemctl", "hibernate"])
        if "/r" in low or "/g" in low:
            return Action(argv=["shutdown", "-r", *later] if later else ["systemctl", "reboot"])
        if "/s" in low or "/p" in low:
            return Action(argv=["shutdown", "-h", *later] if later else ["systemctl", "poweroff"])
        return None
    if prog == "rundll32":
        call = " ".join(low)
        if "setsuspendstate" in call:
            hibernate = re.search(r"setsuspendstate\s*,?\s*(\d)", call)
            return Action(argv=["systemctl", "hibernate" if hibernate and hibernate.group(1) == "1" else "suspend"])
        if "lockworkstation" in call:
            return Action(argv=["loginctl", "lock-session"])
        if "control_rundll" in call or "shell32.dll" in call:
            return Action(role="@settings")
    return None


def read_lnk(path: Path) -> tuple[str, list[str]] | None:
    """Target and arguments of a Windows shortcut (MS-SHLLINK), or None if it isn't one."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 76 or data[:4] != b"\x4c\x00\x00\x00":
        return None
    flags = struct.unpack_from("<I", data, 0x14)[0]
    pos = 76
    if flags & 0x01:  # HasLinkTargetIDList
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    target = ""
    if flags & 0x02:  # HasLinkInfo
        size, header = struct.unpack_from("<II", data, pos)
        info_flags, _vol, base_off, _net, suffix_off = struct.unpack_from("<IIIII", data, pos + 8)
        if info_flags & 0x1:
            def cstr(off: int) -> str:
                end = data.index(b"\0", pos + off)
                return data[pos + off:end].decode("mbcs" if os.name == "nt" else "latin-1", errors="replace")
            target = cstr(base_off) + cstr(suffix_off)
        pos += size
    strings: dict[int, str] = {}
    unicode = bool(flags & 0x80)
    for bit in (0x04, 0x08, 0x10, 0x20, 0x40):  # name, relative path, working dir, arguments, icon
        if flags & bit and pos + 2 <= len(data):
            n = struct.unpack_from("<H", data, pos)[0]
            raw = data[pos + 2:pos + 2 + (n * 2 if unicode else n)]
            strings[bit] = raw.decode("utf-16-le" if unicode else "latin-1", errors="replace")
            pos += 2 + len(raw)
    if not target:  # a shell folder (Recycle Bin) or a packaged app: its CLSID is in the ID list
        for start in (0, 1):  # UTF-16 text at an even or an odd offset
            m = re.search(r"::\{[0-9A-Fa-f-]{36}\}", data[start:].decode("utf-16-le", errors="ignore"))
            if m:
                target = m.group(0)
                break
    return target, windows_argv(strings.get(0x20, ""))


def windows_argv(line: str) -> list[str]:
    """Split a Windows command line as CommandLineToArgvW does: quotes may sit inside an argument
    (--profile-directory="Profile 1"), and 2n backslashes before a quote are n backslashes."""
    out: list[str] = []
    cur: list[str] = []
    quoted = started = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\":
            n = len(line) - i - len(line[i:].lstrip("\\"))
            if i + n < len(line) and line[i + n] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i += n + 1
                else:
                    i += n
                started = True
                continue
            cur.append("\\" * n)
            i += n
            started = True
            continue
        if c == '"':
            quoted = not quoted
            started = True
        elif c in " \t" and not quoted:
            if started:
                out.append("".join(cur))
                cur, started = [], False
        else:
            cur.append(c)
            started = True
        i += 1
    if started:
        out.append("".join(cur))
    return out


def translate(args: list[str], resolve_path: Callable[[str], Path],
              which: Callable[[str], str | None] = shutil.which) -> Action | None:
    """What a Windows command line in a skin action means here; None when it isn't Windows-only
    (an http link, a file of the skin, a Linux program) and should run as written."""
    if not args:
        return None
    prog_raw, rest = args[0].strip(), args[1:]
    low = prog_raw.lower()
    if low.startswith("ms-settings:") or low in ("control", "control.exe"):
        return Action(role="@settings")
    folder = _folder(prog_raw)
    if folder is not None:
        return folder
    base = re.split(r"[\\/]", low)[-1]
    name = base[:-4] if base.endswith(".exe") else base
    if name == "explorer":
        if not rest:
            return Action(uri=Path.home().as_uri())
        return _folder(rest[0]) or Action(uri=resolve_path(rest[0]).as_uri())
    power = _power(name, rest)
    if power is not None:
        return power
    path = resolve_path(prog_raw)
    lnk = path if path.suffix.lower() == ".lnk" else resolve_path(prog_raw + ".lnk")
    if lnk.is_file() and not (path.exists() and path != lnk):
        found = read_lnk(lnk)
        if found is None or not found[0]:
            return Action(note=f"{lnk.name}: a shortcut to a Windows app")
        return translate([found[0], *found[1], *rest], resolve_path, which)
    if path.exists() or re.match(r"^[a-z][a-z0-9+.\-]*://", low):
        return None
    return _program(prog_raw, rest, which)
