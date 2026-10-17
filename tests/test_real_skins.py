"""Regressions found by loading real .rmskin packages (Sonder, ModernGadgets, Fountain of Colors…)."""

from pathlib import Path

import pytest

from helpers import make_skin
from raintux.core.pcre import compile_pcre


def test_current_section_variable(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureFive]
        Measure=Calc
        Formula=5
        IfCondition=[#CURRENTSECTION] = 5
        IfTrueAction=[!SetVariable Who "#CURRENTSECTION#"]
        DynamicVariables=1

        [Variables]
        Who=nobody
    """)
    skin.update()
    assert skin.get_variable("Who") == "MeasureFive"


def test_formatted_time_value_is_the_text_number(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureDay]
        Measure=Time
        Format=%#j

        [MeasureNow]
        Measure=Time
    """)
    day, now = skin.measure("MeasureDay"), skin.measure("MeasureNow")
    assert day.value == float(day.string_value())
    assert now.value.is_integer()  # whole seconds, so OnChangeAction doesn't fire every update


def test_on_change_action_with_update_does_not_loop(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [Variables]
        N=0

        [MeasureCount]
        Measure=Calc
        Formula=#N#
        DynamicVariables=1
        OnChangeAction=[!Update]["changed"]
    """)
    skin.execute("[!SetVariable N 1]")
    skin.update()  # the nested !Update must not see the same change again
    assert host.commands.count("changed") == 1


def test_include_path_with_builtin_variables(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [Rainmeter]
        @Include=#ROOTCONFIGPATH#Parts\\Body.inc

        [MeterBody]
        Meter=String
        MeterStyle=StyleBody
    """, files={"Test/Parts/Body.inc": "[StyleBody]\nText=included\n"})
    assert skin.meter("MeterBody").text == "included"


def test_meter_position_of_a_later_string_meter(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterFirst]
        Meter=String
        X=([MeterSecond:X] + 5)
        DynamicVariables=1

        [MeterSecond]
        Meter=String
        X=20
        StringAlign=Right
        Text=x
    """)
    skin.update()
    second = skin.meter("MeterSecond")
    assert skin.meter("MeterFirst").x == pytest.approx(second.x - second.w + 5, abs=0.01)  # X of a right-aligned box


def test_runaway_meter_does_not_grow_the_window(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterBox]
        Meter=Image
        W=50
        H=20
        SolidColor=255,0,0

        [MeterAway]
        Meter=Image
        X=(1/0.00001*1000000)
        W=10
        H=10
        SolidColor=255,0,0
    """)
    assert (skin.width, skin.height) == (50, 20)


def test_utf16_lua_script(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    lua = "function Update()\n  return loadstring('return 6*7')()\nend\n"  # loadstring: Lua 5.1
    (tmp_path / "Skins" / "Test").mkdir(parents=True)
    (tmp_path / "Skins" / "Test" / "s.lua").write_bytes(b"\xff\xfe" + lua.encode("utf-16-le"))
    skin, _ = make_skin(tmp_path, """
        [MeasureScript]
        Measure=Script
        ScriptFile=s.lua
    """)
    skin.update()
    assert skin.measure("MeasureScript").value == 42


def test_pcre_character_classes() -> None:
    assert compile_pcre(r"[[:alpha:]]+").search("ab1").group(0) == "ab"
    assert compile_pcre(r"[[(]x").search("[x").group(0) == "[x"
    assert compile_pcre(r"[\h,]+").search("a ,\tb").group(0) == " ,\t"


@pytest.mark.gtk
def test_legacy_text_padding_keeps_the_full_width(tmp_path: Path) -> None:
    import cairo

    from raintux.meters import text as T

    skin, _ = make_skin(tmp_path, """
        [MeterDigit]
        Meter=String
        Text=8
        FontSize=35
        W=32
        H=70
        ClipString=1
    """)  # Win10 Widgets' clock digits: AccurateText=0 must not shrink the clip width
    mt = skin.meter("MeterDigit")
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10))
    assert not T.make_layout(cr, mt.text, mt.style).is_ellipsized()


def test_bang_arguments_split_before_section_variables(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureText]
        Measure=String
        String=497.2 M

        [Variables]
        Ram=none
    """)
    skin.update()
    skin.execute("[!SetVariable Ram [MeasureText]]")  # ModernGadgets: the value has a space
    assert skin.get_variable("Ram") == "497.2 M"


def test_empty_set_option_falls_back_to_the_style(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [StyleDay]
        FontColor=255,0,0

        [MeterDay]
        Meter=String
        MeterStyle=StyleDay
        FontColor=0,255,0
        Text=1
    """)
    skin.execute('[!SetOption MeterDay FontColor ""][!UpdateMeter MeterDay]')  # ModernGadgets' calendar
    assert skin.meter("MeterDay").color_ == (1.0, 0.0, 0.0, 1.0)


def test_windows_environment_variables(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureDrive]
        Measure=String
        String=%SystemDrive%
        Substitute=":":""

        [MeasureFormat]
        Measure=String
        String=%H:%M %1 50%
    """)  # DetailedSystemMonitor: Measure[MeasureDrive]Total -> MeasureCTotal
    skin.update()
    assert skin.measure("MeasureDrive").string_value() == "C"
    assert skin.measure("MeasureFormat").string_value() == "%H:%M %1 50%"


@pytest.mark.gtk
def test_partial_transform_order_keeps_the_other_steps(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterIcon]
        Meter=Shape
        Shape=Rectangle 0,0,512,512 | Scale 0.05,0.05,0,0 | Offset 0,10 | TransformOrder Offset
    """)  # DetailedSystemMonitor's icons
    skin.update()
    mt = skin.meter("MeterIcon")
    assert mt.w < 30 and mt.h < 30  # scaled to ~26 px; skipping Scale left it 512 px


def test_lua_reads_crlf_files_like_windows(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    (tmp_path / "Skins" / "Test").mkdir(parents=True)
    (tmp_path / "Skins" / "Test" / "vars.inc").write_bytes(b"[Variables]\r\nConfig=Fountain of Colors\r\n")
    (tmp_path / "Skins" / "Test" / "s.lua").write_text(
        "function Initialize()\n"
        "  for line in io.lines(SKIN:MakePathAbsolute('vars.inc')) do last = line end\n"
        "  local f = io.open(SKIN:MakePathAbsolute('vars.inc'))\n"
        "  first = f:read()\n"
        "  f:close()\n"
        "end\n"
        "function Update() return first .. '|' .. last end\n")
    skin, _ = make_skin(tmp_path, """
        [MeasureScript]
        Measure=Script
        ScriptFile=s.lua
    """)  # Fountain of Colors' Initialize script; a "\r" ended up in !DeactivateConfig
    skin.update()
    assert skin.measure("MeasureScript").string_value() == "[Variables]|Config=Fountain of Colors"


def test_string_measure_value_is_ready_for_meters_read_before_it_updates(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureDrive]
        Measure=String
        String=C:
        Substitute=":":""

        [MeasureCTotal]
        Measure=Calc
        Formula=1234

        [MeterTotal]
        Meter=String
        MeasureName=Measure[MeasureDrive]Total
    """)  # DetailedSystemMonitor's disk total
    skin.update()
    assert skin.meter("MeterTotal").text == "1234"


def test_drive_type_number(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureCType]
        Measure=FreeDiskSpace
        Drive=C:
        Type=1
    """)  # DetailedSystemMonitor disables a drive's measures unless this is 4 (Fixed)
    skin.update()
    m = skin.measure("MeasureCType")
    assert (m.string_value(), m.value) in (("Fixed", 4.0), ("Ram", 7.0), ("Network", 5.0))


def test_meter_position_while_loading_follows_the_relative_chain(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterRow1]
        Meter=Image
        Y=25
        W=10
        H=20

        [MeterRow2]
        Meter=Image
        Y=15R
        W=10
        H=20

        [MeterBelow]
        Meter=Image
        Y=([MeterRow2:Y]+60)
        W=10
        H=10
    """)  # SysDash's settings: read at load, before any layout
    skin.update()
    assert skin.meter("MeterBelow").y == 25 + 20 + 15 + 60


@pytest.mark.gtk
def test_roundline_without_measure_is_full(tmp_path: Path) -> None:
    import cairo

    skin, _ = make_skin(tmp_path, """
        [MeterRing]
        Meter=Roundline
        W=40
        H=40
        LineStart=10
        LineLength=20
        Solid=1
        LineColor=255,255,255
        RotationAngle=(Rad(360))
    """)  # DetailedSystemMonitor's background rings
    skin.update()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 40, 40)
    skin.draw(cairo.Context(surf))
    surf.flush()
    data = surf.get_data()
    assert data[(20 * 40 + 35) * 4 + 3] > 0  # a pixel on the ring, right of the centre


def test_transparent_image_mouse_region_takes_clicks(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterBackground]
        Meter=Image
        W=100
        H=50
        SolidColor=20,20,20

        [MeterStartButton]
        Meter=Image
        X=10
        Y=10
        W=20
        H=20
        SolidColor=255,255,255,0
        LeftMouseUpAction=[!SetVariable Started 1]

        [Variables]
        Started=0
    """)  # ModernGadgets' stopwatch buttons
    skin.mouse("down", 20, 20)  # not consumed: a press may still start a window drag
    assert skin.mouse("up", 20, 20)
    assert skin.get_variable("Started") == "1"
