"""Features of current Rainmeter builds: TextEdit, section bangs, keyed variables, mouse variables,
built-in plugin measures, DragDrop, Mouse, Svg."""

from pathlib import Path

import pytest

from helpers import make_skin


def test_old_plugin_names_and_builtin_measure_types(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureA]
        Measure=Plugin
        Plugin=Plugins\\QuotePlugin.dll
        PathName=q.txt

        [MeasureB]
        Measure=Quote
        PathName=q.txt

        [MeasureC]
        Measure=RecycleManager
        RecycleType=Count
    """, files={"Test/q.txt": "only"})
    assert type(skin.measure("MeasureA")).__name__ == "QuotePlugin"
    assert skin.measure("MeasureB").string_value() == "only"
    assert type(skin.measure("MeasureC")).__name__ == "RecycleManagerPlugin"


def test_keyed_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAINTUX_TEST_ENV", "env-value")
    skin, _ = make_skin(tmp_path, """
        [Variables]
        Color=red

        [MeasureN]
        Measure=Calc
        Formula=7
    """)
    r = skin.replace_section_variables
    assert r("[#Color] [&MeasureN] [%RAINTUX_TEST_ENV] [$SkinW] [$SkinZoomFactor]") == "red 7 env-value 1 1"


def test_mouse_variables_in_actions(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [MeterSlider]
        Meter=Image
        X=100
        Y=10
        W=200
        H=20
        SolidColor=255,255,255
        LeftMouseUpAction=[!SetVariable Pos $MouseX$][!SetVariable Pct "$MouseX:%$"]["at [$MouseY]"]
    """)
    skin.mouse("up", 149, 15)
    assert skin.get_variable("Pos") == "49"
    assert skin.get_variable("Pct") == "25"
    assert host.commands == ["at 5"]


def test_section_bangs_and_skin_prefix(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [MeasureLoop]
        Measure=Loop
        StartValue=1
        EndValue=10
        Group=Anim

        [MeasureTimer]
        Measure=ActionTimer
        ActionList1=Go
        Go=[!SetVariable Ran 1]
        LeftMouseUpAction=[!ActionTimer:Execute 1]

        [Variables]
        Ran=0
    """)
    for _ in range(3):
        skin.update()
    assert skin.measure("MeasureLoop").value == 4
    skin.execute("[!Loop:Reset MeasureLoop]")
    skin.update()
    assert skin.measure("MeasureLoop").value == 1
    skin.execute("[!ActionTimer:Execute MeasureTimer 1]")
    assert skin.get_variable("Ran") == "1"
    skin.execute("[!Skin:Refresh Test][!Skin:Load illustro\\Clock Clock.ini]")
    assert ("activateconfig", ["illustro\\Clock", "Clock.ini"]) in host.host_bangs


def test_mouse_action_disable_enable(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [MeterA]
        Meter=Image
        W=10
        H=10
        SolidColor=1,1,1
        LeftMouseUpAction=["clicked"]
    """)
    skin.execute('[!DisableMouseAction MeterA "LeftMouseUpAction"]')
    skin.mouse("up", 5, 5)
    skin.execute('[!EnableMouseAction MeterA "LeftMouseUpAction"]')
    skin.mouse("up", 5, 5)
    assert host.commands == ["clicked"]


TEXTEDIT_SKIN = """
    [Variables]
    Last=

    [MeterSearch]
    Meter=TextEdit
    X=10
    Y=10
    W=200
    H=20
    FontSize=10
    PlaceholderText=Search...
    MaxLength=12
    OnSubmitAction=[!SetVariable Last "[$Input]"][!TextEdit:Clear]
    OnDismissAction=["dismissed"]
"""


@pytest.mark.gtk
def test_text_edit_typing_and_submit(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, TEXTEDIT_SKIN)
    te = skin.meter("MeterSearch")
    assert te.text == "Search..."  # placeholder drawn while empty
    assert skin.mouse("down", 20, 15)
    assert te.focused and skin.wants_keyboard()
    skin.text_input("hello world!!")
    assert te.edit_text == "hello world!"  # MaxLength=12
    skin.key("BackSpace")
    skin.key("Left", ctrl=True)
    skin.key("Left", shift=True)
    assert te.selected_text() == " "
    skin.text_input("_")
    assert te.edit_text == "hello_world"
    skin.key("z", ctrl=True)
    assert te.edit_text == "hello world"
    skin.key("Return")
    assert skin.get_variable("Last") == "hello world"
    assert te.edit_text == ""
    skin.mouse("down", 500, 500)  # click elsewhere dismisses
    assert not te.focused and host.commands == ["dismissed"]


@pytest.mark.gtk
def test_text_edit_filters_and_bangs(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterNum]
        Meter=TextEdit
        W=100
        H=20
        InputFilter=DECIMAL
        InitialText=1.5
        Password=1
    """)
    te = skin.meter("MeterNum")
    skin.execute("[!TextEdit:Focus MeterNum][!TextEdit:SelectAll MeterNum]")
    assert te.focused and te.selected_text() == "1.5"
    skin.text_input("abc")
    assert te.edit_text == "1.5"  # refused by the filter
    skin.text_input("-2.25")
    assert te.edit_text == "-2.25"
    assert te.display_text() == "●" * 5
    skin.execute("[!TextEdit:Reset MeterNum]")
    assert te.edit_text == "1.5"
    skin.execute('[!TextEdit:SetText MeterNum "7"]')
    assert te.input_variable("") == "7"


def test_drag_drop_measure(tmp_path: Path) -> None:
    drop = tmp_path / "dropped.txt"
    drop.write_text("x")
    target = tmp_path / "Inbox"
    skin, host = make_skin(tmp_path, f"""
        [MeterArea]
        Meter=Image
        W=100
        H=100
        SolidColor=0,0,0,50

        [MeasureDrop]
        Measure=DragDrop
        Action=Copy
        Path={target}
        Bounds=MeterArea
        OnEnterAction=["enter"]
        OnDropAction=["got $Name$.$Type$ #$FileNr$"]
    """)
    assert skin.drop_motion(10, 10)
    assert not skin.drop_motion(150, 10)
    assert skin.drop_files([drop], 50, 50)
    assert (target / "dropped.txt").read_text() == "x"
    assert host.commands == ["enter", "got dropped.txt #1"]


def test_mouse_measure_drag_action(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterTrack]
        Meter=Image
        W=200
        H=10
        SolidColor=255,255,255

        [MeasureMouse]
        Measure=Mouse
        Delay=0
        LeftMouseDragAction=[!SetVariable Knob $MouseX:%$]

        [Variables]
        Knob=0
    """)
    assert skin.mouse("down", 50, 5)  # a drag action takes the press (no window move)
    skin.mouse("drag", 99, 5)
    assert skin.get_variable("Knob") == "50"
    skin.mouse("up", 99, 5)
    skin.mouse("drag", 150, 5)
    assert skin.get_variable("Knob") == "50"


@pytest.mark.gtk
def test_svg_meter(tmp_path: Path) -> None:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
           '<rect id="box" width="40" height="20" fill="red"/></svg>')
    skin, _ = make_skin(tmp_path, """
        [MeterSvg]
        Meter=Svg
        SvgImage=pic.svg
        Attribute=#box | fill | blue
        W=80
    """, files={"Test/pic.svg": svg})
    mt = skin.meter("MeterSvg")
    if mt.renderer is None:
        pytest.skip("no SVG renderer (librsvg) installed")
    assert (mt.w, mt.h) == (80, 40)
    from raintux.meters.svg_meter import apply_attributes

    assert b'fill="blue"' in apply_attributes(svg.encode(), [("#box", "fill", "blue")])
