"""The bits of Windows that skins read directly, answered from Linux.

Skins ask the registry for the CPU name, the Windows accent colour, the date and time formats or
the Windows version (``Measure=Registry``), and run ``reg query`` or ``wmic`` through RunCommand.
This module keeps a small read-only registry of those keys, filled from /proc, os-release, the
locale and the desktop's accent colour, and answers the ``reg`` and ``wmic`` command lines skins
use. Anything else reads as missing, like a key that isn't there.
"""

from __future__ import annotations

import functools
import locale
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

Value = tuple[str, Any]  # (REG_SZ | REG_DWORD | REG_QWORD | REG_BINARY | REG_MULTI_SZ, data)

_HIVES = {
    "hkey_current_user": "HKEY_CURRENT_USER", "hkcu": "HKEY_CURRENT_USER",
    "hkey_local_machine": "HKEY_LOCAL_MACHINE", "hklm": "HKEY_LOCAL_MACHINE",
    "hkey_classes_root": "HKEY_CLASSES_ROOT", "hkcr": "HKEY_CLASSES_ROOT",
    "hkey_users": "HKEY_USERS", "hku": "HKEY_USERS", "hkey_current_config": "HKEY_CURRENT_CONFIG",
    "hkcc": "HKEY_CURRENT_CONFIG",
}

# Windows 10's own palette for its default blue (#0078D7): three lighter shades, the accent, three
# darker ones and a complementary colour, as stored in Explorer\Accent\AccentPalette.
_DEFAULT_PALETTE = ["a6d8ff", "76b9ed", "429ce3", "0078d7", "005a9e", "004275", "002642", "f7630c"]
_GNOME_ACCENTS = {"blue": (0x35, 0x84, 0xE4), "teal": (0x21, 0x90, 0xA4), "green": (0x3A, 0x94, 0x4A),
                  "yellow": (0xC8, 0x88, 0x00), "orange": (0xED, 0x5B, 0x00), "red": (0xE6, 0x2D, 0x42),
                  "pink": (0xD5, 0x61, 0x99), "purple": (0x91, 0x41, 0xAC), "slate": (0x6F, 0x83, 0x96)}


# --------------------------------------------------------------------------- facts about this PC
def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@functools.cache
def _cpuinfo() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _read("/proc/cpuinfo").splitlines():
        if not line.strip():
            if out:
                break  # the first processor is enough
            continue
        k, _sep, v = line.partition(":")
        out.setdefault(k.strip().lower(), v.strip())
    return out


def cpu_name() -> str:
    info = _cpuinfo()
    return info.get("model name") or info.get("hardware") or info.get("cpu model") or platform.processor()


def cpu_mhz() -> int:
    """Nominal clock in MHz, as ``~MHz`` gives it (the highest frequency, else the current one)."""
    khz = _read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq").strip()
    if khz.isdigit():
        return int(khz) // 1000
    try:
        return int(float(_cpuinfo().get("cpu mhz", "0")))
    except ValueError:
        return 0


def cpu_identifier() -> str:
    info = _cpuinfo()
    arch = {"GenuineIntel": "Intel64", "AuthenticAMD": "AMD64"}.get(info.get("vendor_id", ""), platform.machine())
    return f"{arch} Family {info.get('cpu family', '0')} Model {info.get('model', '0')} Stepping {info.get('stepping', '0')}"


@functools.cache
def os_release() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (_read("/etc/os-release") or _read("/usr/lib/os-release")).splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k.strip()] = v.strip().strip('"')
    return out


@functools.cache
def gpu_names() -> list[str]:
    """Display adapters, as ``wmic path win32_VideoController get name`` lists them."""
    names: list[str] = []
    for info in sorted(Path("/proc/driver/nvidia/gpus").glob("*/information")):
        m = re.search(r"^Model:\s*(.+)$", _read(str(info)), re.M)
        if m:
            names.append(m.group(1).strip())
    if not names and shutil.which("lspci"):
        try:
            out = subprocess.run(["lspci", "-mm"], capture_output=True, text=True, timeout=3, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            f = re.findall(r'"([^"]*)"', line)
            if len(f) >= 3 and re.search(r"VGA|3D|Display", f[0]):
                vendor = re.sub(r"\s*(Corporation|Corp\.|Inc\.|\[AMD/ATI\])", "", f[1]).strip()
                model = f[2].split("[")[-1].rstrip("]") if "[" in f[2] else f[2]
                names.append(f"{vendor} {model}".strip())
    return names


def _accent_from_desktop() -> tuple[int, int, int] | None:
    kde = Path.home() / ".config" / "kdeglobals"
    m = re.search(r"^AccentColor\s*=\s*(\d+),\s*(\d+),\s*(\d+)", _read(str(kde)), re.M)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    name = _gsetting("org.gnome.desktop.interface", "accent-color")
    return _GNOME_ACCENTS.get(name)


def _gsetting(schema: str, key: str) -> str:
    if not shutil.which("gsettings"):
        return ""
    try:
        out = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, timeout=2,
                             check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.strip().strip("'")


@functools.cache
def accent_rgb() -> tuple[int, int, int] | None:
    """The desktop's accent colour (KDE, GNOME 47+), or None for Windows' default blue."""
    return _accent_from_desktop()


@functools.cache
def light_theme() -> bool:
    """Apps use a light theme (Personalize\\AppsUseLightTheme); Windows' default when unknown."""
    scheme = _gsetting("org.gnome.desktop.interface", "color-scheme")
    if scheme:
        return scheme != "prefer-dark"
    theme = os.environ.get("GTK_THEME", "") or _gsetting("org.gnome.desktop.interface", "gtk-theme")
    return not theme.lower().endswith("dark") if theme else True


def accent_palette() -> list[tuple[int, int, int]]:
    rgb = accent_rgb()
    if rgb is None:
        return [tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) for h in _DEFAULT_PALETTE]  # type: ignore[misc]
    mix = lambda c, t, f: tuple(round(a + (b - a) * f) for a, b in zip(c, t))  # noqa: E731
    white, black = (255, 255, 255), (0, 0, 0)
    comp = tuple(255 - c for c in rgb)
    return [mix(rgb, white, 0.65), mix(rgb, white, 0.45), mix(rgb, white, 0.25), rgb,  # type: ignore[list-item]
            mix(rgb, black, 0.25), mix(rgb, black, 0.45), mix(rgb, black, 0.7), comp]


def _abgr(rgb: tuple[int, int, int], alpha: int = 0xFF) -> int:
    r, g, b = rgb
    return (alpha << 24) | (b << 16) | (g << 8) | r


# --------------------------------------------------------------------------- date and time formats
_STRFTIME_TO_WINDOWS = {"Y": "yyyy", "y": "yy", "m": "MM", "d": "dd", "e": "d", "b": "MMM", "h": "MMM",
                        "B": "MMMM", "a": "ddd", "A": "dddd", "H": "HH", "k": "H", "I": "hh", "l": "h",
                        "M": "mm", "S": "ss", "p": "tt", "P": "tt", "R": "HH:mm", "T": "HH:mm:ss",
                        "r": "hh:mm:ss tt", "D": "MM/dd/yy", "F": "yyyy-MM-dd", "%": "%"}


def windows_format(fmt: str) -> str:
    """A strftime format (the locale's D_FMT / T_FMT) in Windows' date/time picture syntax."""
    return re.sub(r"%[-_0^#]?([A-Za-z%])", lambda m: _STRFTIME_TO_WINDOWS.get(m.group(1), ""), fmt).strip()


def _langinfo(item: str, default: str) -> str:
    try:
        return locale.nl_langinfo(getattr(locale, item)) or default
    except (AttributeError, ValueError):
        return default


def international() -> dict[str, Value]:
    """``HKCU\\Control Panel\\International`` from the locale (LC_TIME)."""
    t_fmt = windows_format(_langinfo("T_FMT", "%H:%M:%S"))
    short_time = re.sub(r"[:.]ss", "", t_fmt)
    d_fmt = windows_format(_langinfo("D_FMT", "%m/%d/%y"))
    order = [c for c in re.findall(r"[dMy]", d_fmt)]
    long_date = {"M": "dddd, MMMM d, yyyy", "d": "dddd, d MMMM yyyy"}.get(order[0] if order else "M", "dddd, yyyy MMMM d")
    sep = re.search(r"[^dMy]", d_fmt)
    twelve = "tt" in t_fmt
    return {
        "sShortTime": ("REG_SZ", short_time), "sTimeFormat": ("REG_SZ", t_fmt),
        "sShortDate": ("REG_SZ", d_fmt), "sLongDate": ("REG_SZ", long_date),
        "sDate": ("REG_SZ", sep.group(0) if sep else "/"), "sTime": ("REG_SZ", ":"),
        "iTime": ("REG_SZ", "0" if twelve else "1"), "s1159": ("REG_SZ", "AM" if twelve else ""),
        "s2359": ("REG_SZ", "PM" if twelve else ""),
        "sDecimal": ("REG_SZ", locale.localeconv().get("decimal_point") or "."),
        "sThousand": ("REG_SZ", locale.localeconv().get("thousands_sep") or ","),
        "LocaleName": ("REG_SZ", (locale.getlocale(locale.LC_TIME)[0] or "en_US").replace("_", "-")),
    }


# --------------------------------------------------------------------------- the registry
def _cpu_key(index: int) -> dict[str, Value] | None:
    if index >= (os.cpu_count() or 1):
        return None
    info = _cpuinfo()
    return {"ProcessorNameString": ("REG_SZ", cpu_name()), "~MHz": ("REG_DWORD", cpu_mhz()),
            "Identifier": ("REG_SZ", cpu_identifier()), "VendorIdentifier": ("REG_SZ", info.get("vendor_id", ""))}


def _windows_nt() -> dict[str, Value]:
    rel = os_release()
    kernel = platform.release().split("-")[0]
    try:
        import pwd

        owner = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0] or pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        owner = os.environ.get("USER", "")
    return {"ProductName": ("REG_SZ", rel.get("PRETTY_NAME") or rel.get("NAME") or platform.system()),
            "EditionID": ("REG_SZ", rel.get("VARIANT", "")), "DisplayVersion": ("REG_SZ", rel.get("VERSION_ID", "")),
            "ReleaseId": ("REG_SZ", rel.get("VERSION_ID", "")), "CurrentBuild": ("REG_SZ", kernel),
            "CurrentBuildNumber": ("REG_SZ", kernel), "CurrentVersion": ("REG_SZ", "6.3"),
            "RegisteredOwner": ("REG_SZ", owner), "InstallationType": ("REG_SZ", "Client")}


def _accent() -> dict[str, Value]:
    pal = accent_palette()
    return {"AccentPalette": ("REG_BINARY", bytes(b for rgb in pal for b in (*rgb, 0))),
            "AccentColorMenu": ("REG_DWORD", _abgr(pal[3])), "StartColorMenu": ("REG_DWORD", _abgr(pal[4]))}


def _dwm() -> dict[str, Value]:
    rgb = accent_palette()[3]
    argb = (0xC4 << 24) | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
    return {"ColorizationColor": ("REG_DWORD", argb), "AccentColor": ("REG_DWORD", _abgr(rgb)),
            "ColorPrevalence": ("REG_DWORD", 0), "EnableAeroPeek": ("REG_DWORD", 1)}


def _personalize() -> dict[str, Value]:
    light = 1 if light_theme() else 0
    return {"AppsUseLightTheme": ("REG_DWORD", light), "SystemUsesLightTheme": ("REG_DWORD", light),
            "EnableTransparency": ("REG_DWORD", 1), "ColorPrevalence": ("REG_DWORD", 0)}


def _winsat() -> dict[str, Value]:
    gpus = gpu_names()
    return {"PrimaryAdapterString": ("REG_SZ", gpus[0] if gpus else "")}


_KEYS: dict[str, Callable[[], dict[str, Value]]] = {
    r"hkey_local_machine\software\microsoft\windows nt\currentversion": _windows_nt,
    r"hkey_local_machine\software\microsoft\windows nt\currentversion\winsat": _winsat,
    r"hkey_current_user\control panel\international": international,
    r"hkey_current_user\software\microsoft\windows\dwm": _dwm,
    r"hkey_current_user\software\microsoft\windows\currentversion\explorer\accent": _accent,
    r"hkey_current_user\software\microsoft\windows\currentversion\themes\personalize": _personalize,
}
_CPU_KEY = re.compile(r"^hkey_local_machine\\hardware\\description\\system\\centralprocessor\\(\d+)$")


def split_key(hkey: str, key: str = "") -> tuple[str, str]:
    """("HKEY_LOCAL_MACHINE", "path") from RegHKey / RegKey, or from one ``HKLM\\path`` string."""
    key = key.replace("/", "\\").strip().strip("\\")
    hive = _HIVES.get(hkey.strip().lower())
    if hive is None and not key:
        first, _sep, key = hkey.replace("/", "\\").strip().partition("\\")
        hive = _HIVES.get(first.lower(), first.upper())
    return hive or "HKEY_CURRENT_USER", key


def read_key(hkey: str, key: str = "") -> dict[str, Value] | None:
    """The values of a key (names as Windows spells them), or None when there is no such key."""
    hive, path = split_key(hkey, key)
    full = f"{hive}\\{path}".lower().rstrip("\\")
    m = _CPU_KEY.match(full)
    if m:
        return _cpu_key(int(m.group(1)))
    fn = _KEYS.get(full)
    return fn() if fn else None


def subkeys(hkey: str, key: str = "") -> list[str]:
    hive, path = split_key(hkey, key)
    full = f"{hive}\\{path}".lower()
    if full == r"hkey_local_machine\hardware\description\system\centralprocessor":
        return [str(i) for i in range(os.cpu_count() or 1)]
    prefix = full + "\\"
    names = {k[len(prefix):].split("\\")[0] for k in _KEYS if k.startswith(prefix)}
    return sorted(names)


def read_value(hkey: str, key: str, name: str) -> Value | None:
    values = read_key(hkey, key) or {}
    return next((v for k, v in values.items() if k.lower() == name.lower()), None)


# --------------------------------------------------------------------------- console commands
def _format_value(kind: str, data: Any) -> str:
    if kind in ("REG_DWORD", "REG_QWORD"):
        return hex(int(data))
    if kind == "REG_BINARY":
        return bytes(data).hex().upper()
    if kind == "REG_MULTI_SZ":
        return "\\0".join(data)
    return str(data)


def reg_query(args: list[str]) -> tuple[int, str]:
    """``reg query KEY [/v NAME | /ve] [/s]`` as reg.exe prints it."""
    if not args:
        return 1, "ERROR: Invalid syntax.\nType \"REG QUERY /?\" for usage.\n"
    hive, path = split_key(args[0])
    want: str | None = None
    lower = [a.lower() for a in args]
    if "/v" in lower and lower.index("/v") + 1 < len(args):
        want = args[lower.index("/v") + 1]
    values = read_key(hive, path)
    subs = subkeys(hive, path)
    missing = "ERROR: The system was unable to find the specified registry key or value.\n"
    if values is None and not subs:
        return 1, missing
    shown = [(k, v) for k, v in (values or {}).items() if want is None or k.lower() == want.lower()]
    if want is not None and not shown:
        return 1, missing
    lines = ["", f"{hive}\\{path}"]
    lines += [f"    {k}    {kind}    {_format_value(kind, data)}" for k, (kind, data) in shown]
    lines.append("")
    if want is None:
        lines += [f"{hive}\\{path}\\{s}" for s in subs]
        if subs:
            lines.append("")
    return 0, "\n".join(lines) + "\n"


def wmic(args: list[str]) -> tuple[int, str] | None:
    """The WMI queries skins make: video controller, CPU and OS names, thermal zone temperatures."""
    words = [a for a in args if not a.startswith("/")]
    low = [w.lower() for w in words]
    if "get" not in low:
        return None
    cls = " ".join(low[:low.index("get")]).replace("path ", "").strip()
    props = [p.strip() for p in " ".join(words[low.index("get") + 1:]).split(",") if p.strip()]
    rows: list[dict[str, Any]]
    if cls in ("win32_videocontroller", "gpu"):
        rows = [{"name": n, "caption": n, "description": n} for n in gpu_names()]
    elif cls in ("cpu", "win32_processor"):
        rows = [{"name": cpu_name(), "maxclockspeed": cpu_mhz(), "currentclockspeed": cpu_mhz(),
                 "numberofcores": len(set(_cores())) or os.cpu_count() or 1,
                 "numberoflogicalprocessors": os.cpu_count() or 1,
                 "manufacturer": _cpuinfo().get("vendor_id", "")}]
    elif cls in ("os", "win32_operatingsystem"):
        rows = [{"caption": _windows_nt()["ProductName"][1], "version": platform.release(),
                 "osarchitecture": "64-bit" if platform.machine().endswith("64") else "32-bit"}]
    elif cls == "msacpi_thermalzonetemperature":
        rows = [{"currenttemperature": round((t + 273.15) * 10)} for t in _thermal_zones()]
    else:
        return None
    if not props:
        return None
    header = "  ".join(p.capitalize() if p.islower() else p for p in props)
    lines = [header] + ["  ".join(str(r.get(p.lower(), "")) for p in props) for r in rows]
    return 0, "\n".join(lines) + "\n\n"


def _cores() -> list[str]:
    return re.findall(r"^core id\s*:\s*(\d+)", _read("/proc/cpuinfo"), re.M)


def _thermal_zones() -> list[float]:
    temps = []
    for z in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            temps.append(int(z.read_text().strip()) / 1000.0)
        except (OSError, ValueError):
            continue
    return temps


def emulate(argv: list[str]) -> tuple[int, str] | None:
    """(exit code, output) for a Windows command this module answers, else None (run it)."""
    if not argv:
        return None
    prog = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    prog = prog[:-4] if prog.endswith(".exe") else prog
    if prog == "reg" and len(argv) > 1 and argv[1].lower() == "query":
        return reg_query(argv[2:])
    if prog == "wmic":
        return wmic(argv[1:])
    return None
