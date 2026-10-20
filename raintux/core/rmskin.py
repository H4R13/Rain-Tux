"""Read and install Rainmeter ``.rmskin`` packages (zip + ``RMSKIN.ini`` + footer).

Old packages from Rainmeter 1.x / 2.0 ("Rainstaller", still installed by Rainmeter today) keep
their files under a root folder holding ``Rainstaller.cfg``, layouts as ``Themes/<Name>/*.thm``
and fonts in ``Fonts/``; see DialogInstall.cpp.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from raintux.compat.ini_normalizer import normalize_text
from raintux.core.ini_writer import detect_encoding, write_key_value

log = logging.getLogger(__name__)


@dataclass
class PackageInfo:
    name: str = ""
    author: str = ""
    version: str = ""
    load: str = ""  # e.g. "illustro\\Clock\\Clock.ini"
    load_type: str = "Skin"
    variable_files: list[str] = field(default_factory=list)
    merge_skins: bool = False
    skins: list[str] = field(default_factory=list)  # top-level skin folders in the package
    layouts: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    python_scripts: bool = False  # a skin has ScriptFile=*.py: it installs into the PythonSkins folder
    load_skins: list[str] = field(default_factory=list)  # LoadType=Skin: every skin to load (old packages list several)
    old_format: bool = False  # Rainstaller.cfg package
    root: tuple[str, ...] = ()  # folder inside the zip that holds Skins/, Layouts/ (Themes/), ...


_PY_SCRIPT = re.compile(r"^\s*ScriptFile\s*=\s*\"?[^\r\n\"]*\.py\"?\s*$", re.I | re.M)


def _read_ini(text: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    cur: dict[str, str] | None = None
    for line in normalize_text(text).split("\n"):
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = out.setdefault(s[1:-1].strip().lower(), {})
        elif "=" in s and cur is not None:
            k, _sep, v = s.partition("=")
            cur[k.strip().lower()] = v.strip()
    return out


def _safe_parts(name: str) -> tuple[str, ...] | None:
    """Zip member path split into parts, or None for anything that could escape the target."""
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or any(part in ("..", "") for part in p.parts) or (p.parts and ":" in p.parts[0]):
        return None
    return p.parts


def _under_root(name: str, root: tuple[str, ...]) -> tuple[str, ...] | None:
    """Parts of a zip member below the package root, or None (outside it, or unsafe)."""
    parts = _safe_parts(name)
    if not parts or len(parts) <= len(root):
        return None
    if any(a.lower() != b.lower() for a, b in zip(parts, root)):
        return None
    return parts[len(root):]


def _read_options(info: PackageInfo, zf: zipfile.ZipFile, names: list[str]) -> None:
    ini_name = next((n for n in names if n.lower() == "rmskin.ini"), None)
    if ini_name:
        raw = zf.read(ini_name)
        sec = _read_ini(raw.decode(detect_encoding(raw), errors="replace")).get("rmskin", {})
        info.name = sec.get("name", "")
        info.author = sec.get("author", "")
        info.version = sec.get("version", "")
        info.load = sec.get("load", "")
        info.load_type = sec.get("loadtype", "Skin") or "Skin"
        info.variable_files = [v.strip() for v in sec.get("variablefiles", "").split("|") if v.strip()]
        info.merge_skins = sec.get("mergeskins", "0").strip() == "1"
        info.load_skins = [info.load] if info.load and info.load_type.lower() == "skin" else []
        return
    cfgs = [p for p in (_safe_parts(n) for n in names) if p and p[-1].lower() == "rainstaller.cfg"]
    if not cfgs:
        return
    cfg = min(cfgs, key=len)
    info.old_format, info.root = True, cfg[:-1]
    raw = zf.read(next(n for n in names if _safe_parts(n) == cfg))
    sec = _read_ini(raw.decode(detect_encoding(raw), errors="replace")).get("rainstaller", {})
    unquote = lambda v: v.strip().strip('"').strip()  # noqa: E731
    info.name = sec.get("name", "")
    info.author = sec.get("author", "")
    info.version = sec.get("version", "")
    info.merge_skins = sec.get("merge", "0").strip() == "1"
    info.variable_files = [v.strip() for v in sec.get("keepvar", "").split("|") if v.strip()]
    launch, command = sec.get("launchtype", "").strip().lower(), unquote(sec.get("launchcommand", ""))
    if launch == "load":
        info.load_type = "Skin"
        info.load_skins = [unquote(v) for v in command.split("|") if unquote(v)]
        info.load = info.load_skins[0] if info.load_skins else ""
    elif launch and command:
        info.load_type, info.load = "Layout", command


def open_package(path: Path) -> tuple[PackageInfo, zipfile.ZipFile]:
    """Open an ``.rmskin`` (or plain zip) and read its ``RMSKIN.ini`` (or old ``Rainstaller.cfg``)."""
    zf = zipfile.ZipFile(path)
    info = PackageInfo()
    names = zf.namelist()
    _read_options(info, zf, names)
    layouts_dir, layout_ext = ("themes", ".thm") if info.old_format else ("layouts", "")
    for n in names:
        parts = _under_root(n, info.root)
        if not parts or len(parts) < 2:
            continue
        top = parts[0].lower()
        if top == "skins" and parts[1] not in info.skins and len(parts) >= 3:
            info.skins.append(parts[1])
        elif top == layouts_dir and parts[1] not in info.layouts and n.lower().endswith(layout_ext):
            info.layouts.append(parts[1])
        elif top == "plugins" and n.lower().endswith(".dll"):
            info.plugins.append(parts[-1])
        if n.lower().endswith((".ttf", ".otf")):
            info.fonts.append(parts[-1])
        if top == "skins" and n.lower().endswith(".ini") and not info.python_scripts:
            raw = zf.read(n)
            info.python_scripts = bool(_PY_SCRIPT.search(raw.decode(detect_encoding(raw), errors="replace")))
    if not info.name and info.skins:
        info.name = info.skins[0]
    return info, zf


def install_root(info: PackageInfo, skins_root: Path, python_root: Path) -> Path:
    """The folder a package's skins go into: Python-scripted skins apart from the INI + Lua ones."""
    return python_root if info.python_scripts else skins_root


def _preserve_variables(old_root: Path, new_root: Path, rel_files: list[str]) -> None:
    """Keep user-edited values in ``VariableFiles`` (Rainmeter behaviour on upgrade)."""
    for rel in rel_files:
        rel_path = Path(*rel.replace("\\", "/").split("/"))
        old_f, new_f = old_root / rel_path, new_root / rel_path
        if not old_f.is_file() or not new_f.is_file():
            continue
        old_raw = old_f.read_bytes()
        new_raw = new_f.read_bytes()
        old_ini = _read_ini(old_raw.decode(detect_encoding(old_raw), errors="replace"))
        new_ini = _read_ini(new_raw.decode(detect_encoding(new_raw), errors="replace"))
        for sec, keys in new_ini.items():
            for k in keys:
                if k in old_ini.get(sec, {}) and old_ini[sec][k] != keys[k]:
                    write_key_value(new_f, sec, k, old_ini[sec][k])


def install_package(path: Path, skins_root: Path, layouts_root: Path | None = None) -> PackageInfo:
    """Extract skins (backing up replaced folders into ``@Backup``), layouts and fonts.

    Windows plugins (``.dll``) cannot run and are skipped.
    """
    info, zf = open_package(path)
    skins_root.mkdir(parents=True, exist_ok=True)
    staging = skins_root / f".install-{int(time.time() * 1000)}"
    staging.mkdir()
    try:
        for member in zf.infolist():
            parts = _under_root(member.filename, info.root)
            if not parts or member.is_dir():
                continue
            top = parts[0].lower()
            dests: list[Path] = []
            if top == "skins" and len(parts) >= 3:
                dests = [staging.joinpath(*parts[1:])]
            elif top == "layouts" and not info.old_format and layouts_root is not None and len(parts) >= 3:
                dests = [layouts_root.joinpath(*parts[1:])]
            elif (top == "themes" and info.old_format and layouts_root is not None and len(parts) >= 3
                  and parts[-1].lower().endswith(".thm")):  # an old layout: Themes/<Name>/Rainmeter.thm
                dests = [layouts_root.joinpath(*parts[1:-1], parts[-1][:-4] + ".ini")]
            elif top == "fonts" and info.old_format and len(parts) == 2 and parts[1].lower().endswith(".ttf"):
                dests = [staging / skin / "@Resources" / "Fonts" / parts[1] for skin in info.skins]
            for dest in dests:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        backup_root = skins_root / "@Backup"
        for skin in info.skins:
            new = staging / skin
            if not new.is_dir():
                continue
            target = skins_root / skin
            if target.exists():
                if info.merge_skins:
                    shutil.copytree(new, target, dirs_exist_ok=True)
                    continue
                backup_root.mkdir(exist_ok=True)
                backup = backup_root / f"{skin}-{time.strftime('%Y%m%d-%H%M%S')}"
                shutil.move(str(target), str(backup))
                shutil.move(str(new), str(target))
                _preserve_variables(backup, target, [v.split("\\", 1)[1] if v.lower().startswith(skin.lower() + "\\") else v
                                                     for v in info.variable_files])
            else:
                shutil.move(str(new), str(target))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        zf.close()
    if info.plugins:
        log.warning("%s ships Windows plugins that cannot run on Linux: %s", info.name, ", ".join(sorted(set(info.plugins))))
    return info


def is_package(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return any(n.lower() == "rmskin.ini" or n.lower().endswith("rainstaller.cfg") for n in zf.namelist())
    except zipfile.BadZipFile:
        return False
