"""Build throwaway skins on disk and run them headlessly."""

from __future__ import annotations

import textwrap
from pathlib import Path

from raintux.core.skin import NullHost, Skin


def make_skin(tmp_path: Path, body: str, name: str = "Test", files: dict[str, str] | None = None,
              start: bool = True) -> tuple[Skin, NullHost]:
    """Write ``Skins/<name>/<name>.ini`` (+ extra files) and load it with a NullHost."""
    root = tmp_path / "Skins" / name
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in (files or {}).items():
        p = tmp_path / "Skins" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")
    ini = root / f"{name}.ini"
    ini.write_text(textwrap.dedent(body), encoding="utf-8")
    host = NullHost()
    skin = Skin(ini, host=host, skins_root=tmp_path / "Skins")
    host.skins[skin.config.lower()] = skin
    skin.load()
    if start:
        skin.start()
    return skin, host
