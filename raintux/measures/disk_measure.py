"""``Measure=FreeDiskSpace`` — free bytes on a drive (``Total=1``, ``Type=1``, ``Label=1``)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import psutil

from raintux.measures.base_measure import Measure


_DRIVE_MAP: dict[str, str] = {}  # the [drives] table of config.toml: "D" -> "/home"
_MNT = Path("/mnt")
_is_mount = os.path.ismount
_PARTITIONS: dict[bool, tuple[float, list]] = {}  # all -> (when, psutil.disk_partitions(all))
_PARTITIONS_TTL = 5.0


def partitions(all_: bool = False) -> list:
    """``psutil.disk_partitions``, kept for a few seconds: it calls pathconf on every mount, and
    every drive measure of a skin asks on each update (ModernGadgets' DisksMeter has dozens)."""
    now = time.monotonic()
    when, parts = _PARTITIONS.get(all_, (-_PARTITIONS_TTL, []))
    if now - when >= _PARTITIONS_TTL:
        parts = psutil.disk_partitions(all=all_)
        _PARTITIONS[all_] = (now, parts)
    return parts


def set_drive_map(mapping: dict[str, str]) -> None:
    """Use the user's drive letter -> folder table (keys like ``D`` or ``D:``)."""
    _DRIVE_MAP.clear()
    _DRIVE_MAP.update({k.strip().rstrip(":").upper(): v for k, v in mapping.items() if k.strip()})


def map_drive(drive: str) -> str:
    """Translate a Windows ``Drive=`` into a Linux mount point.

    A letter set in the user's ``[drives]`` table wins. Otherwise ``C:`` is the root
    filesystem, a letter mounted at ``/mnt/<letter>`` uses that mount (WSL shows Windows
    drives there), and the remaining letters take the other mounts under /media, /mnt and
    /run/media in order (``D:`` = the first). Anything that is already a path is used as-is.
    """
    d = drive.strip().strip('"').replace("\\", "/")
    if d.startswith(("/", "~")):
        return os.path.expanduser(d)
    if len(d) >= 2 and d[1] == ":":
        letter = d[0].upper()
        if letter in _DRIVE_MAP:
            return os.path.expanduser(_DRIVE_MAP[letter])
        if letter == "C":
            return "/"
        own = _MNT / letter.lower()
        if _is_mount(str(own)):
            return str(own)
        extra = sorted(
            p.mountpoint
            for p in partitions()
            if p.mountpoint.startswith(("/media", "/mnt", "/run/media"))
            and not p.mountpoint.startswith("/mnt/wsl")
            and not (len(p.mountpoint) == 6 and p.mountpoint.startswith("/mnt/"))  # /mnt/x: a letter's own
        )
        idx = ord(letter) - ord("D")
        if 0 <= idx < len(extra):
            return extra[idx]
        return ""
    return d or "/"


# MeasureDiskSpace.cpp's DRIVETYPE_* values.
_DRIVE_TYPES = {"Error": 0, "Removed": 1, "Removable": 3, "Fixed": 4, "Network": 5, "CDRom": 6, "Ram": 7}


class DiskMeasure(Measure):
    """Number is free bytes (used with ``InvertMeasure=1`` in many skins)."""

    def read_measure(self) -> None:
        self.path = map_drive(self.get("drive", "C:"))
        self.total = self.get_bool("total")
        self.type_mode = self.get_bool("type")
        self.label_mode = self.get_bool("label")
        self.ignore_removable = self.get_bool("ignoreremovable", True)

    def update_value(self) -> None:
        if self.type_mode or self.label_mode:
            self.string = self._type_or_label()
            # Type=1 also gives Rainmeter's number (skins test e.g. "= 4" for a fixed drive).
            self._raw_number = float(_DRIVE_TYPES.get(self.string, 0)) if self.type_mode else 0.0
            if self.type_mode and not self.has("maxvalue"):
                self.max_value = 7.0
            return
        if not self.path or not Path(self.path).exists():
            self._raw_number = 0.0
            return
        usage = psutil.disk_usage(self.path)
        if not self.has("maxvalue"):
            self.max_value = float(usage.total)
        self._raw_number = float(usage.total if self.total else usage.free)

    def _type_or_label(self) -> str:
        if not self.path:
            return "Removed" if self.type_mode else ""
        part = next(
            (p for p in sorted(partitions(True), key=lambda p: -len(p.mountpoint))
             if self.path.startswith(p.mountpoint)),
            None,
        )
        if self.label_mode:
            return Path(self.path).name or "/"
        if part is None:
            return "Error"
        if part.fstype in ("nfs", "nfs4", "cifs", "smb3", "sshfs", "fuse.sshfs"):
            return "Network"
        if self.path.startswith(("/media", "/run/media")):
            return "Removable"
        if part.fstype in ("iso9660", "udf"):
            return "CDRom"
        if part.fstype in ("tmpfs", "ramfs"):
            return "Ram"
        return "Fixed"
