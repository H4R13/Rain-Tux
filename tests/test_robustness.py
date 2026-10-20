"""Regression tests from bug-hunting passes."""

from pathlib import Path

import pytest

from helpers import make_skin
from raintux.core.config import load_config
from raintux.core.layouts import list_layouts, read_layout, save_layout
from raintux.core.paths import resolve_ci, windows_to_posix
from raintux.core.state import AppState


def test_show_meter_survives_dynamic_reread(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterA]
        Meter=Image
        W=10
        H=10
        Hidden=1
        DynamicVariables=1
    """)
    a = skin.meter("MeterA")
    assert a.hidden
    skin.execute("[!ShowMeter MeterA]")
    skin.update()
    skin.update()
    assert not a.hidden  # Rainmeter writes Hidden=0 along with the bang
    skin.execute("[!HideMeter MeterA]")
    skin.update()
    assert a.hidden


def test_relative_to_hidden_meter_collapses(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterA]
        Meter=Image
        X=10
        W=50
        H=10
        Hidden=1

        [MeterB]
        Meter=Image
        X=5R
        W=10
        H=10
    """)
    assert skin.meter("MeterB").x == 15  # hidden previous meter has width 0


def test_background_margins_and_image_size(tmp_path: Path) -> None:
    pil = pytest.importorskip("PIL.Image")
    (tmp_path / "Skins" / "Test").mkdir(parents=True)
    pil.new("RGBA", (210, 68), (0, 0, 0, 200)).save(tmp_path / "Skins" / "Test" / "bg.png")
    skin, _ = make_skin(tmp_path, """
        [Rainmeter]
        Background=bg.png
        BackgroundMode=3
        BackgroundMargins=0,34,0,14

        [MeterA]
        Meter=Image
        X=10
        Y=40
        W=190
        H=60
    """)
    assert (skin.width, skin.height) == (210, 114)


def test_lua_watchdog_stops_runaway_script(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    skin, _ = make_skin(tmp_path, """
        [MeasureScript]
        Measure=Script
        ScriptFile=loop.lua
    """, files={"Test/loop.lua": "function Update() while true do end end\n"})
    skin.update()  # returns instead of hanging forever
    assert skin.measure("MeasureScript").value == 0


def test_windows_paths_case_insensitive(tmp_path: Path) -> None:
    d = tmp_path / "Skins" / "Test" / "Images"
    d.mkdir(parents=True)
    (d / "Background.PNG").write_bytes(b"x")
    assert windows_to_posix(r"C:\Users\x\a.png") == "/Users/x/a.png"
    assert resolve_ci(tmp_path / "Skins" / "test" / "images" / "background.png") == d / "Background.PNG"
    skin, _ = make_skin(tmp_path, "[Variables]\nA=1\n")
    assert skin.path(r"Images\BACKGROUND.png") == d / "Background.PNG"


def test_include_with_wrong_case_and_backslashes(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [Variables]
        @Include=Lib\\VARS.inc
    """, files={"Test/lib/vars.inc": "[Variables]\nFromInclude=yes\n"})
    assert skin.get_variable("FromInclude") == "yes"


def test_layouts_roundtrip_and_rainmeter_ini(tmp_path: Path) -> None:
    st = AppState()
    cs = st.config(r"illustro\Clock")
    cs.active_file = "Clock.ini"
    cs.window.x, cs.window.y = 12, 34
    save_layout(tmp_path, "My: Layout", st)
    assert list_layouts(tmp_path) == ["My_ Layout"]
    got = read_layout(tmp_path, "My_ Layout")
    assert got[r"illustro\Clock"].active_file == "Clock.ini" and got[r"illustro\Clock"].window.x == 12
    rm = tmp_path / "Old" / "Rainmeter.ini"
    rm.parent.mkdir()
    rm.write_text("[Rainmeter]\nConfigEditor=x\n\n[illustro\\System]\nActive=2\nWindowX=100\nWindowY=200\n"
                  "AlphaValue=200\nClickThrough=1\n\n[illustro\\Off]\nActive=0\n")
    got = read_layout(tmp_path, "Old", lambda c: ["A.ini", "System.ini"])
    assert list(got) == [r"illustro\System"]
    s = got[r"illustro\System"]
    assert (s.active_file, s.window.x, s.window.y, s.window.alpha, s.window.click_through) == ("System.ini", 100, 200, 200, True)


def test_broken_config_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "config.toml"
    cfg.write_text("[api\nthis is not toml")
    c = load_config(cfg)
    assert c.api.port == 7272 and c.engine.skins_roots
    assert (tmp_path / "config.toml.broken").exists()
    assert "[ui]" in cfg.read_text()


def test_mouse_variable_escape(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [MeterA]
        Meter=Image
        W=100
        H=100
        SolidColor=1,1,1
        LeftMouseUpAction=["$*MouseX*$ $MouseX$ $NotMouse$"]
    """)
    skin.mouse("up", 7, 9)
    assert host.commands == ["$MouseX$ 7 $NotMouse$"]


def test_action_recursion_is_bounded(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureLoop]
        Measure=Calc
        Formula=1
        OnUpdateAction=[!UpdateMeasure MeasureLoop]
    """)
    skin.update()  # must terminate
