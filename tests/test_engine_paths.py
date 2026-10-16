"""Skin-id containment check, without a running GTK app."""

from pathlib import Path
from types import SimpleNamespace

import pytest

engine_mod = pytest.importorskip("raintux.core.engine", reason="needs GTK 4 bindings")


def _engine(root: Path):
    eng = object.__new__(engine_mod.RainTuxEngine)
    eng.config = SimpleNamespace(engine=SimpleNamespace(skins_roots=[str(root)]))
    return eng


def test_resolve_ini_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_mod, "default_skins_roots", lambda: [])
    root = tmp_path / "Skins"
    (root / "Minimal").mkdir(parents=True)
    (root / "Minimal" / "skin.ini").write_text("[Rainmeter]\n")
    (tmp_path / "evil.ini").write_text("[Rainmeter]\n")
    eng = _engine(root)
    assert eng.resolve_ini("Minimal") == (root / "Minimal" / "skin.ini").resolve()
    assert eng.resolve_ini("Minimal/skin.ini") is not None
    assert eng.resolve_ini("../evil.ini") is None
    assert eng.resolve_ini("Minimal/../../evil.ini") is None
    assert eng.resolve_ini(str(tmp_path / "evil.ini")) is None
