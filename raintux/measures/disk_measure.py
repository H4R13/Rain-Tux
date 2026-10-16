"""``Measure=FreeDiskSpace`` — free bytes on a drive (``Total=1``, ``Type=1``, ``Label=1``)."""

from __future__ import annotations

import os
from pathlib import Path

import psutil

from raintux.measures.base_measure import Measure


def map_drive(drive: str) -> str:
    """Translate a Windows ``Drive=`` into a Linux mount point.

    ``C:`` is the root filesystem; other letters map to removable/extra mounts in
    order (``D:`` = first non-root mount under /media, /mnt or /run/media), and
    anything that is already a path is used as-is.
    """
    d = drive.strip().strip('"').replace("\\", "/")
    if d.startswith(("/", "~")):
        return os.path.expanduser(d)
    if len(d) >= 2 and d[1] == ":":
        letter = d[0].upper()
        if letter == "C":
            return "/"
        extra = sorted(
            p.mountpoint
            for p in psutil.disk_partitions(all=False)
            if p.mountpoint.startswith(("/media", "/mnt", "/run/media"))
        )
        idx = ord(letter) - ord("D")
        if 0 <= idx < len(extra):
            return extra[idx]
        return ""
    return d or "/"


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
            self._raw_number = 0.0
            self.string = self._type_or_label()
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
            (p for p in sorted(psutil.disk_partitions(all=True), key=lambda p: -len(p.mountpoint))
             if self.path.startswith(p.mountpoint)),
            None,
        )
        if self.label_mode:
            return Path(self.path).name or "/"
        if part is None:
            return "Unknown"
        if part.fstype in ("nfs", "nfs4", "cifs", "smb3", "sshfs", "fuse.sshfs"):
            return "Network"
        if self.path.startswith(("/media", "/run/media")):
            return "Removable"
        if part.fstype in ("iso9660", "udf"):
            return "CDRom"
        if part.fstype in ("tmpfs", "ramfs"):
            return "Ram"
        return "Fixed"
