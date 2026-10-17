"""Skin window positions as Rainmeter writes them (WindowX/WindowY/AnchorX/AnchorY), and bangs
sent from outside RainTux."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import make_skin
from raintux.core.layouts import read_layout, save_layout
from raintux.core.position import (
    anchor_offset,
    apply_skin_defaults,
    authored_position,
    resolve_position,
    with_modifiers,
)
from raintux.core.skin import WindowState
from raintux.core.state import AppState, window_state

SCREEN = [(0, 0, 1920, 1080)]

# Layouts/Win10 Widgets Default/Rainmeter.ini from the Win10 Widgets .rmskin (comments cut).
WIN10_LAYOUT = r"""[Rainmeter]

[Win10 Widgets\DateTime]
Active=1
WindowX=(#WORKAREAX# + #WORKAREAWIDTH# - 361)
WindowY=(#WORKAREAY# + 45)
AutoSelectScreen=1

[Win10 Widgets\Battery]
Active=1
WindowX=(#WORKAREAX# + #WORKAREAWIDTH# - 361)
WindowY=(#WORKAREAY# + 45 + 122 + 10)
AutoSelectScreen=1

[Win10 Widgets\HardDrive]
Active=2
WindowX=(#WORKAREAX# + #WORKAREAWIDTH# - 361)
WindowY=(#WORKAREAY# + 45 + 122 + 10 + 78 + 10)

[Win10 Widgets\WiFi]
Active=3
WindowX=(#WORKAREAX# + #WORKAREAWIDTH# - 361)
WindowY=(#WORKAREAY# + 45 + 122 + 10 + 78 + 10 + 65 + 10)

[Win10 Widgets\Volume]
Active=3
WindowX=(#WORKAREAX# + #WORKAREAWIDTH# - 361)
WindowY=(#WORKAREAY# + 45 + 122 + 10 + 78 + 10 + 65 + 10 + 68 + 10)

[Win10 Widgets\Performance - Combo]
Active=1
WindowX=(#WORKAREAX#+15)
WindowY=(#WORKAREAY#+#WORKAREAHEIGHT#-212-15)

[Win10 Widgets\Weather]
Active=1
WindowX=(#WORKAREAX# + 15 + 212 + 15)
WindowY=(#WORKAREAY# + #WORKAREAHEIGHT# - 212 - 15)

[Win10 Widgets\Welcome]
Active=1
WindowX=(#WORKAREAX# + ((#WORKAREAWIDTH# - 800) / 2))
WindowY=(#WORKAREAY# + ((#WORKAREAHEIGHT# - 525) / 2))
"""


def _win10(tmp_path: Path, monitors: list[tuple[int, int, int, int]]) -> dict[str, tuple[int, int]]:
    ini = tmp_path / "Win10 Widgets Default" / "Rainmeter.ini"
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_bytes(WIN10_LAYOUT.replace("\n", "\r\n").encode("utf-16"))  # as shipped: UTF-16 with BOM
    got = read_layout(tmp_path, "Win10 Widgets Default", lambda c: ["A.ini", "B.ini", "C.ini"], monitors)
    return {cfg.split("\\")[1]: (cs.window.x, cs.window.y) for cfg, cs in got.items()}


def test_layout_window_positions_can_be_formulas(tmp_path: Path) -> None:
    pos = _win10(tmp_path, SCREEN)
    assert pos == {
        "DateTime": (1559, 45), "Battery": (1559, 177), "HardDrive": (1559, 265), "WiFi": (1559, 340),
        "Volume": (1559, 418), "Performance - Combo": (15, 853), "Weather": (242, 853), "Welcome": (560, 277),
    }


def test_layout_formulas_use_the_primary_monitor(tmp_path: Path) -> None:
    # The first monitor is the primary one, even when it isn't at the desktop's 0,0.
    pos = _win10(tmp_path, [(1920, 0, 2560, 1440), (0, 0, 1920, 1080)])
    assert pos["DateTime"] == (1920 + 2560 - 361, 45)
    assert pos["Welcome"] == (1920 + 880, 457)


def test_layout_keeps_window_options_as_written(tmp_path: Path) -> None:
    rm = tmp_path / "Sonder" / "Rainmeter.ini"
    rm.parent.mkdir()
    rm.write_text("[Sonder\\Time]\nActive=1\nWindowX=(#SCREENAREAWIDTH#/2)\nAnchorX=50%\nWindowY=100\n")
    w = read_layout(tmp_path, "Sonder", lambda c: ["Time.ini"])["Sonder\\Time"].window
    assert (w.window_x, w.window_y, w.anchor_x, w.anchor_y) == ("(#SCREENAREAWIDTH#/2)", "100", "50%", "0")
    assert authored_position(w, (300, 80), SCREEN) == (810, 100)
    st = AppState()
    st.config("Sonder\\Time").active_file = "Time.ini"
    st.config("Sonder\\Time").window = w
    save_layout(tmp_path, "Mine", st)
    again = read_layout(tmp_path, "Mine")["Sonder\\Time"].window
    assert (again.window_x, again.anchor_x) == ("(#SCREENAREAWIDTH#/2)", "50%")


@pytest.mark.parametrize(("wx", "wy", "ax", "ay", "expected"), [
    ("R", "B", "0", "0", (1920, 1080)),  # left / top edge on the right / bottom edge
    ("260R", "305B", "0", "0", (1660, 775)),
    ("162R", "305B", "50%", "0", (1608, 775)),
    ("50%", "28.5%", "0", "0", (960, 307)),
    ("50.00000%", "0", "50%", "50%", (810, 0 - 40)),
    ("0R", "0B", "R", "B", (1620, 1000)),
    ("20R", "0", "20R", "0", (1620, 0)),
    ("(100 + 20)R", "(10*2)", "0", "0", (1800, 20)),  # formula, then the modifiers after it
    ("(#SCREENAREAWIDTH#/2)", "(#SCREENAREAHEIGHT#/2)", "50%", "50%", (810, 500)),
    ("-50", "1.9", "0", "0", (-50, 1)),
    ("#OPTR#", "0", "0", "0", (0, 0)),  # an R inside a variable that was left is no modifier
    ("(1 +", "x", "0", "0", (0, 0)),
])
def test_window_position_options(wx: str, wy: str, ax: str, ay: str, expected: tuple[int, int]) -> None:
    assert resolve_position(wx, wy, ax, ay, (300, 80), SCREEN) == expected


def test_window_position_on_another_monitor() -> None:
    mons = [(0, 0, 1920, 1080), (1920, 100, 1280, 1024)]
    assert resolve_position("100@2", "50", size=(300, 80), monitors=mons) == (2020, 150)  # Y follows X's @2
    assert resolve_position("0", "10B@2", size=(300, 80), monitors=mons) == (1920, 100 + 1024 - 10)
    assert resolve_position("0R@0", "0@0", monitors=mons) == (3200, 0)  # @0: the whole desktop
    assert resolve_position("10@5", "10", monitors=mons) == (10, 10)  # no monitor 5: the primary
    assert resolve_position("(#WORKAREAX@2# + 10)", "(#SCREENAREAY@2#)", monitors=mons) == (1930, 100)
    assert resolve_position("(#VSCREENAREAWIDTH#)", "(#PSCREENAREAHEIGHT#)", monitors=mons) == (3200, 1080)


def test_formula_with_modifiers() -> None:
    assert with_modifiers("(1/2)") == "0.5"
    assert with_modifiers("(10*2)") == "20"
    assert with_modifiers("(3)R@2") == "3R@2"
    assert with_modifiers("(1+)") == "(1+)"
    assert with_modifiers("10 + (5)") == "10 + (5)"  # a formula has to start with "("
    assert with_modifiers("100") == "100"


def test_anchor_offsets() -> None:
    assert anchor_offset("50%", 300) == 150
    assert anchor_offset("R", 300) == 300
    assert anchor_offset("20R", 300) == 280
    assert anchor_offset("B", 80, opposite="B") == 80
    assert anchor_offset("10", 300, zoom=2.0) == 20
    assert anchor_offset("(10*3)", 300) == 30


def test_saved_state_with_text_positions() -> None:
    w = window_state({"x": "(#WORKAREAWIDTH# - 361)", "y": 45, "alpha": 200, "unknown": 1})
    assert (w.x, w.y, w.window_x, w.window_y, w.alpha) == (0, 45, "(#WORKAREAWIDTH# - 361)", None, 200)
    assert authored_position(w, (361, 122), SCREEN) == (1559, 45)
    assert authored_position(window_state({"x": 12.0, "y": 34}), (10, 10), SCREEN) is None
    assert window_state({"x": 12.0, "y": 34}).x == 12


def test_skin_default_window_options(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [Rainmeter]
        DefaultWindowX=50.0%
        DefaultWindowY=(#Top# + 10)
        DefaultAnchorX=50.0%
        DefaultAlwaysOnTop=1
        DefaultDraggable=0
        DefaultKeepOnScreen=0

        [Variables]
        Top=40

        [Box]
        Meter=Image
        W=200
        H=100
        SolidColor=0,0,0
    """)
    st = WindowState()
    apply_skin_defaults(st, skin.rm.option_raw, SCREEN, skin.resolve)
    assert (st.window_x, st.window_y, st.anchor_x, st.anchor_y) == ("50.0%", "50", "50.0%", "0")
    assert (st.z, st.draggable, st.keep_on_screen, st.snap_edges) == (1, False, False, True)
    assert authored_position(st, (skin.width, skin.height), SCREEN) == (860, 50)


# ---------------------------------------------------------------------------- engine


def _run_idle() -> None:
    from gi.repository import GLib

    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass


@pytest.mark.gtk
def test_app_bangs_from_outside_run_with_no_skin_loaded() -> None:
    from raintux.core.engine import RainTuxEngine

    calls: list[tuple] = []
    eng = SimpleNamespace(active={}, show_manage=lambda c=None: calls.append(("manage", c)),
                          load_layout=lambda n: calls.append(("layout", n)),
                          _safe_activate=lambda c, f: calls.append(("activate", c, f)))
    eng.host_bang = partial(RainTuxEngine.host_bang, eng)
    RainTuxEngine.execute_global(eng, '!LoadLayout "Win10 Widgets Default"')  # type: ignore[arg-type]
    RainTuxEngine.execute_global(eng, r"!ActivateConfig illustro\Clock Clock.ini")  # type: ignore[arg-type]
    RainTuxEngine.execute_global(eng, r"!Manage Skins illustro\Clock")  # type: ignore[arg-type]
    _run_idle()
    assert calls == [("manage", r"illustro\Clock"), ("layout", "Win10 Widgets Default"),
                     ("activate", r"illustro\Clock", "Clock.ini")]


@pytest.mark.gtk
def test_app_bangs_from_outside_run_with_many_skins_loaded() -> None:
    from raintux.core.engine import RainTuxEngine

    calls: list[tuple] = []
    eng = SimpleNamespace(active={"a": object(), "b": object()},
                          host_bang=lambda skin, name, args: calls.append((skin, name, args)))
    RainTuxEngine.execute_global(eng, "!ShowGroup Widgets")  # type: ignore[arg-type]
    RainTuxEngine.execute_global(eng, "!RainmeterLoadLayout Mine")  # type: ignore[arg-type]
    assert calls == [(None, "showgroup", ["Widgets"]), (None, "loadlayout", ["Mine"])]


@pytest.mark.gtk
def test_engine_places_skins_from_window_options(tmp_path: Path) -> None:
    from raintux.core.engine import RainTuxEngine

    skin, _ = make_skin(tmp_path, "[Box]\nMeter=Image\nW=200\nH=100\nSolidColor=0,0,0\n")
    st = skin.state

    def move_to(x: int, y: int) -> None:
        st.x, st.y = x, y

    act = SimpleNamespace(skin=skin, window=SimpleNamespace(zoom=1.0, move_to=move_to), size=None)
    eng = SimpleNamespace(_monitor_rects=lambda: SCREEN,
                          clamp_on_screen=lambda w, x, y: (min(x, 1920 - skin.width), min(y, 1080 - skin.height)))
    eng._place_from_options = partial(RainTuxEngine._place_from_options, eng)
    assert not eng._place_from_options(act)  # moved since: nothing written to work out
    st.window_x, st.window_y = "R", "B"
    assert eng._place_from_options(act)
    assert (st.x, st.y) == (1720, 980)  # KeepOnScreen pulls a skin placed at "R" / "B" back in

    # The skin grows: WindowX / WindowY as written are worked out again for the new size.
    st.window_x, st.window_y, st.anchor_x = "(#SCREENAREAWIDTH#/2)", "0", "50%"
    act.size = (200, 100)
    skin.width = 300
    RainTuxEngine._resized(eng, act, 300, 100)  # type: ignore[arg-type]
    assert (st.x, st.y) == (810, 0)

    # Moved since (no options to go by): the anchor point stays where it was.
    st.window_x = st.window_y = None
    st.x = 500
    skin.width = 400
    RainTuxEngine._resized(eng, act, 400, 100)  # type: ignore[arg-type]
    assert (st.x, st.y) == (450, 0)  # centre still at 650
