"""The skins RainTux installs on first launch (raintux/data/skins)."""

import logging
from pathlib import Path

import pytest

from helpers import make_skin
from raintux.core.skin import NullHost, Skin

DATA = Path(__file__).resolve().parent.parent / "raintux" / "data" / "skins"
ILLUSTRO = DATA / "illustro"


def _inis(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.ini") if "@Resources" not in p.parts)


def test_illustro_is_bundled_as_the_raintux_edition() -> None:
    assert {p.parent.name for p in _inis(ILLUSTRO)} == {"Clock", "Disk", "Google", "Network", "System", "Trash", "Welcome"}
    license_text = (ILLUSTRO / "LICENSE.txt").read_text(encoding="utf-8")
    assert "poiru" in license_text and "by-nc-sa/3.0" in license_text
    for ini in _inis(ILLUSTRO):
        text = ini.read_text(encoding="utf-8")
        assert "License=Creative Commons BY-NC-SA 3.0" in text and "Author=poiru" in text
        assert "Rainmeter\\" not in text and "taskmgr" not in text and "C:" not in text
    welcome = (ILLUSTRO / "Welcome" / "Welcome.ini").read_text(encoding="utf-8")
    assert "Text=Welcome to RainTux!" in welcome and "tray icon" not in welcome
    # A right-click must not empty the Trash without asking (Rainmeter's EmptyBin asks first).
    assert "EmptyBin" not in (ILLUSTRO / "Trash" / "Trash.ini").read_text(encoding="utf-8")


@pytest.mark.gtk
@pytest.mark.parametrize("ini", [p for p in _inis(DATA) if p.parent.name != "Network"], ids=lambda p: p.parent.name)
def test_bundled_skins_load_without_warnings(ini: Path, caplog: pytest.LogCaptureFixture) -> None:
    # (illustro's Network skin is left out: its WebParser would fetch an address over the network.)
    caplog.set_level(logging.WARNING)
    skin = Skin(ini, host=NullHost(), skins_root=DATA)
    skin.load()
    skin.start()
    skin.update()
    assert skin.width > 0 and skin.height > 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], [r.getMessage() for r in caplog.records]


def test_python_script_is_reported_not_run_as_lua(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    skin, _ = make_skin(tmp_path, """
        [MeasureScript]
        Measure=Script
        ScriptFile=weather.py
    """, files={"Test/weather.py": "def Update():\n    return 1\n"})
    skin.update()
    assert any("Python skin scripting is not available yet" in r.getMessage() for r in caplog.records)
    assert skin.measure("MeasureScript").value == 0
