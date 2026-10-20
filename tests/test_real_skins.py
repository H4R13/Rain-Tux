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


def test_lua_file_paths_written_for_windows(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    skin, _ = make_skin(tmp_path, """
        [MeasureScript]
        Measure=Script
        ScriptFile=#@#Scripts\\Main.lua
    """, files={
        "Test/@Resources/Scripts/helper.lua": "function Twice(x) return 2 * x end\n",
        "Test/@Resources/Scripts/util.lua": "return {name = 'util'}\n",
        "Test/@Resources/Data/Values.txt": "21\n",
        "Test/@Resources/Scripts/Main.lua": """
            local res = SKIN:GetVariable('@')
            function Initialize()
              dofile(res .. 'SCRIPTS\\\\Helper.lua')
              local f = io.open(res .. 'data\\\\values.TXT')
              n = tonumber(f:read())
              f:close()
              package.path = res .. 'Scripts\\\\?.lua;' .. package.path
              name = require('Util').name .. loadfile(res .. 'scripts\\\\UTIL.lua')().name
              local w = io.open(res .. 'DATA\\\\Out.txt', 'w')
              w:write('saved')
              w:close()
            end
            function Update() return Twice(n) .. name end
        """,
    })  # skins build paths as SKIN:GetVariable('@')..'Scripts\\Lib.lua', in whatever case
    skin.update()
    assert skin.measure("MeasureScript").string_value() == "42utilutil"
    assert (tmp_path / "Skins" / "Test" / "@Resources" / "Data" / "Out.txt").read_text() == "saved"


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


def test_lua_tolua_cast_and_set_option(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    skin, _ = make_skin(tmp_path, """
        [Variables]
        Color=0,0,255

        [MeasureCalc]
        Measure=Calc
        Formula=1

        [MeterA]
        Meter=String
        Group=Labels
        Text=a

        [MeterB]
        Meter=String
        Group=Labels
        Text=b

        [MeasureScript]
        Measure=Script
        ScriptFile=s.lua
    """, files={"Test/s.lua": """
        function Initialize()
          local calc = tolua.cast(SKIN:GetMeasure('MeasureCalc'), 'CMeasure')  -- pre-3.0 scripts
          calc:SetOption('Formula', '(2+3)')
          SKIN:SetOption('MeterA', 'FontColor', '#Color#')
          SKIN:SetOptionGroup('Labels', 'FontSize', 20)
          SKIN:SetOption('MeterB', {'X', '7'}, {'Y', 8}, {5, 'skipped'}, 'skipped')
          SKIN:SetOption('Group=Labels', 'Prefix', '>')
          SKIN:GetMeter('MeterA'):SetOption('Postfix', '<')
          cast_name = calc:GetName()
        end
    """})
    a, b = skin.meter("MeterA"), skin.meter("MeterB")
    assert a.option_raw("FontColor") == "#Color#"  # stored raw, resolved when the meter reads it
    skin.update()
    assert skin.measure("MeasureScript")._lua.globals().cast_name == "MeasureCalc"
    assert skin.measure("MeasureCalc").value == 5
    assert a.color_ == (0.0, 0.0, 1.0, 1.0)
    assert a.size == b.size == 20
    assert (b.option_raw("X"), b.option_raw("Y"), b.option_raw("5")) == ("7", "8", None)
    assert (a.text, b.text) == (">a<", ">b")


def test_lua_set_variable_and_window_calls(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    skin, host = make_skin(tmp_path, """
        [MeasureScript]
        Measure=Script
        ScriptFile=s.lua
    """, files={"Test/s.lua": """
        function Initialize()
          SKIN:SetVariable('Sum', '(2*3)')
          SKIN:SetVariable({'Name', 'x y'}, {'Ratio', 1.5}, {'Skipped'})
          SKIN:MoveWindow(10.7, '20')
          SKIN:FadeWindow(255, 0)
        end
    """})
    assert [skin.get_variable(n) for n in ("Sum", "Name", "Ratio", "Skipped")] == ["6", "x y", "1.5", None]
    assert ("move", ["10", "20"]) in host.host_bangs
    assert ("fadewindow", ["255", "0"]) in host.host_bangs


@pytest.mark.gtk
def test_fade_window_fades_between_the_given_values(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from raintux.core.engine import RainTuxEngine

    calls: list[tuple] = []
    window = SimpleNamespace(set_visible=lambda v: calls.append(("visible", v)),
                             set_opacity=lambda v: calls.append(("opacity", v)),
                             fade_to=lambda v, ms: calls.append(("fade", v, ms)))
    skin, _ = make_skin(tmp_path, "[MeterA]\nMeter=Image\n")
    skin.state.fade_duration = 300
    act = SimpleNamespace(skin=skin, window=window)
    engine = SimpleNamespace(_act=lambda s: act, active={})
    RainTuxEngine.host_bang(engine, skin, "fadewindow", ["0", "255"])  # type: ignore[arg-type]
    assert calls == [("visible", True), ("opacity", 0.0), ("fade", 1.0, 300)]


def test_lua_read_and_write_text_files(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    root = tmp_path / "Skins" / "Test"
    (root / "Data").mkdir(parents=True)
    (root / "Data" / "Notes.txt").write_bytes(b"\xff\xfe" + "line1\r\nlíne2".encode("utf-16-le"))
    skin, _ = make_skin(tmp_path, """
        [MeasureScript]
        Measure=Script
        ScriptFile=s.lua
    """, files={"Test/s.lua": """
        function Initialize()
          notes = SKIN:ReadTextFile('data\\\\NOTES.txt')
          missing, missing_err = SKIN:ReadTextFile('nope.txt')
          ok = SKIN:WriteTextFile('out.txt', 'a\\nb\\r\\nc')
          back = SKIN:ReadTextFile('out.txt')
          ansi_ok = SKIN:WriteTextFile('ansi.txt', 'é', 'ansi')
          bad_ok, bad_err = SKIN:WriteTextFile('bad.txt', 'x', 'UTF-32')
          nodir_ok, nodir_err = SKIN:WriteTextFile('no\\\\dir\\\\x.txt', 'x')
        end
    """})
    g = skin.measure("MeasureScript")._lua.globals()
    assert g.notes == "line1\nlíne2"  # BOM-detected encoding, CRLF read as LF
    assert g.missing is None and g.missing_err == f"Unable to read file: {root / 'nope.txt'}"
    assert g.ok is True and g.back == "a\nb\nc"
    assert (root / "out.txt").read_bytes() == b"\xef\xbb\xbfa\r\nb\r\nc"  # UTF-8 BOM, CRLF
    assert g.ansi_ok is True and (root / "ansi.txt").read_bytes() == b"\xe9"
    assert g.bad_ok is False and g.bad_err == "Unknown encoding: UTF-32" and not (root / "bad.txt").exists()
    assert g.nodir_ok is False and g.nodir_err.startswith("Unable to write file: ")


def test_lua_meter_set_text(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    skin, _ = make_skin(tmp_path, """
        [MeasureCalc]
        Measure=Calc
        Formula=7

        [MeasureScript]
        Measure=Script
        ScriptFile=s.lua

        [MeterText]
        Meter=String
        MeasureName=MeasureCalc
        Text=old %1

        [MeterDynamic]
        Meter=String
        Text=kept
        DynamicVariables=1

        [MeterImage]
        Meter=Image
    """, files={"Test/s.lua": """
        function Update()
          SKIN:GetMeter('MeterText'):SetText('new #X# %1')
          SKIN:GetMeter('MeterDynamic'):SetText('lost')  -- re-read every update, as in Rainmeter
          SKIN:GetMeter('MeterImage'):SetText('ignored')
          return 0
        end
    """})
    skin.update()
    skin.update()
    assert skin.meter("MeterText").text == "new #X# 7"
    assert skin.meter("MeterDynamic").text == "kept"
    skin.execute("[!SetOption MeterText Text again][!UpdateMeter MeterText]")  # a re-read drops it
    assert skin.meter("MeterText").text == "again"


@pytest.mark.gtk
def test_lua_text_edit_set_text(tmp_path: Path) -> None:
    pytest.importorskip("lupa")
    skin, _ = make_skin(tmp_path, """
        [MeterInput]
        Meter=TextEdit
        W=100
        H=20

        [MeasureScript]
        Measure=Script
        ScriptFile=s.lua
    """, files={"Test/s.lua": "function Initialize() SKIN:GetMeter('MeterInput'):SetText('typed') end\n"})
    skin.update()
    assert skin.meter("MeterInput").edit_text == "typed"


def test_windows_drive_letters_map_to_linux_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from raintux.measures import disk_measure as dm

    wsl_x = tmp_path / "mnt" / "x"
    wsl_x.mkdir(parents=True)
    monkeypatch.setattr(dm, "_MNT", tmp_path / "mnt")
    monkeypatch.setattr(dm, "_is_mount", lambda p: p == str(wsl_x))
    monkeypatch.setattr(dm, "_DRIVE_MAP", {})
    assert dm.map_drive("C:") == "/"
    assert dm.map_drive("x:\\") == str(wsl_x)  # WSL shows Windows drives at /mnt/<letter>
    dm.set_drive_map({"d:": str(tmp_path), "C": "/srv"})  # config.toml [drives] wins
    assert (dm.map_drive("D:"), dm.map_drive("C:")) == (str(tmp_path), "/srv")
    skin, _ = make_skin(tmp_path, """
        [MeasureDisk]
        Measure=FreeDiskSpace
        Drive=D:
        Total=1
    """)  # illustro's "2 Disks" on a PC whose user set D = their data folder
    skin.update()
    assert skin.measure("MeasureDisk").value > 0


def test_drive_table_round_trips_through_config_toml(tmp_path: Path) -> None:
    import tomllib

    from raintux.core.config import RainTuxConfig, save_config

    save_config(RainTuxConfig(drives={"D": "/home", "x:": "/mnt/data"}), tmp_path / "config.toml")
    back = RainTuxConfig.model_validate(tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8")))
    assert back.drives == {"D": "/home", "X": "/mnt/data"}
