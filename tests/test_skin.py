"""Skin runtime behaviour with a headless host: update cycle, bangs, variables, actions."""

from pathlib import Path

from helpers import make_skin


def test_calc_sees_earlier_measures_in_same_cycle(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureA]
        Measure=Calc
        Formula=Counter * 10

        [MeasureB]
        Measure=Calc
        Formula=MeasureA + 1
    """)
    skin.update()
    assert skin.measure("MeasureA").value == 20
    assert skin.measure("MeasureB").value == 21


def test_update_divider_and_update_measure_bang(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureEvery3]
        Measure=Calc
        Formula=Counter
        UpdateDivider=3

        [MeasureOnce]
        Measure=Calc
        Formula=Counter
        UpdateDivider=-1
    """)
    for _ in range(5):
        skin.update()
    assert skin.measure("MeasureEvery3").value == 2  # ticks 1 and 4
    assert skin.measure("MeasureOnce").value == 1
    skin.execute("[!UpdateMeasure MeasureOnce]")
    assert skin.measure("MeasureOnce").value == 2


def test_if_conditions_fire_on_change_only(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [MeasureN]
        Measure=Calc
        Formula=Counter % 4
        IfCondition=MeasureN >= 2
        IfTrueAction=[!SetVariable Hits (#Hits#+1)]
        IfCondition2=(MeasureN = 1) && (#Mode# = 7)
        IfTrueAction2=["mode-seven"]
        IfAboveValue=2
        IfAboveAction=["above"]
        OnChangeAction=[!SetVariable Changes (#Changes#+1)]
        DynamicVariables=1

        [Variables]
        Hits=0
        Changes=0
        Mode=7
    """)
    for _ in range(7):
        skin.update()
    # Counter 1..8 -> values 1,2,3,0,1,2,3,0 ; condition true on entering 2 (twice)
    assert skin.get_variable("Hits") == "2"
    assert host.commands.count("above") == 2
    assert host.commands.count("mode-seven") == 2
    assert skin.get_variable("Changes") == "7"


def test_substitute_and_regexp_substitute(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeasureText]
        Measure=String
        String=Hello World 2026
        Substitute="Hello":"Bye","World":"Moon"

        [MeasureRe]
        Measure=String
        String=Hello World 2026
        RegExpSubstitute=1
        Substitute="^(\\w+) .*?(\\d+)$":"\\2-\\1"
    """)
    assert skin.measure("MeasureText").string_value() == "Bye Moon 2026"
    assert skin.measure("MeasureRe").string_value() == "2026-Hello"


def test_section_variables(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [Variables]
        Idx=2
        Name2=two

        [MeasureBig]
        Measure=Calc
        Formula=1536
        MaxValue=2048

        [MeasureStr]
        Measure=String
        String=a+b c

        [MeterBox]
        Meter=Image
        X=10
        Y=20
        W=30
        H=40
    """)
    r = skin.replace_section_variables
    assert r("[MeasureBig]") == "1536"
    assert r("[MeasureBig:%]") == "75"
    assert r("[MeasureBig:/1024,2]") == "1.50"
    assert r("[MeasureBig:MaxValue]") == "2048"
    assert r("[MeasureStr:EncodeURL]") == "a%2Bb%20c"
    assert r("[MeterBox:X] [MeterBox:W] [MeterBox:XW] [MeterBox:YH]") == "10 30 40 60"
    assert r("[#Name[#Idx]]") == "two"  # nested variables resolve inside out
    assert r("[*MeasureBig*] [Unknown]") == "[MeasureBig] [Unknown]"
    assert skin.resolve("[\\x41][\\66]") == "AB"


def test_set_variable_and_dynamic_meter(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [Variables]
        W=50

        [MeterStatic]
        Meter=Image
        W=#W#
        H=10

        [MeterDynamic]
        Meter=Image
        W=#W#
        H=10
        DynamicVariables=1
    """)
    skin.execute("[!SetVariable W (#W#*2)]")
    skin.update()
    assert skin.get_variable("W") == "100"
    assert skin.meter("MeterStatic").w == 50
    assert skin.meter("MeterDynamic").w == 100
    skin.execute('[!SetOption MeterStatic W "70"][!UpdateMeter MeterStatic]')
    assert skin.meter("MeterStatic").w == 70


def test_show_hide_toggle_and_groups(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterA]
        Meter=Image
        W=10
        H=10
        Group=G1 | G2

        [MeterB]
        Meter=Image
        W=10
        H=10
        Hidden=1
        Group=G2
    """)
    a, b = skin.meter("MeterA"), skin.meter("MeterB")
    skin.execute("[!HideMeterGroup G2]")
    assert a.hidden and b.hidden
    skin.execute("[!ShowMeter MeterB][!ToggleMeter MeterA]")
    skin.update()  # Hidden=1 must not undo !ShowMeter on a non-dynamic meter
    assert not a.hidden and not b.hidden
    skin.execute("[!SetOption MeterB Hidden 1]")
    skin.update()
    assert b.hidden


def test_delay_and_config_targeted_bangs(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [Variables]
        A=0
    """, name="One")
    other, _ = make_skin(tmp_path, "[Variables]\nB=0\n", name="Two")
    host.skins[other.config.lower()] = other
    skin.execute("[!SetVariable A 1][!Delay 100][!SetVariable A 2][!SetVariable B 5 Two]")
    assert skin.get_variable("A") == "1"
    host.run_timers()
    assert skin.get_variable("A") == "2"
    assert other.get_variable("B") == "5"


def test_host_bangs_and_commands(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, "[Variables]\nX=1\n")
    skin.execute('[!Move 10 20][!Refresh]["https://example.com"][!ActivateConfig "illustro\\Clock" Clock.ini]')
    assert host.host_bangs[0] == ("move", ["10", "20"])
    assert host.host_bangs[1] == ("refresh", [])
    assert host.host_bangs[2] == ("activateconfig", ["illustro\\Clock", "Clock.ini"])
    assert host.commands == ["https://example.com"]


def test_write_key_value_bang(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, "[Variables]\nCount=1\n")
    skin.execute("[!WriteKeyValue Variables Count (#Count#+4)]")
    assert "Count=5" in skin.ini_path.read_text()


def test_on_refresh_and_on_update_actions(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [Rainmeter]
        OnRefreshAction=[!SetVariable Refreshed 1]
        OnUpdateAction=[!SetVariable Ticks ([MeasureC])]

        [Variables]
        Refreshed=0
        Ticks=0

        [MeasureC]
        Measure=Calc
        Formula=Counter
    """)
    skin.update()
    assert skin.get_variable("Refreshed") == "1"
    assert skin.get_variable("Ticks") == "2"


def test_mouse_actions_hover_and_fallthrough(tmp_path: Path) -> None:
    skin, host = make_skin(tmp_path, """
        [Rainmeter]
        LeftMouseUpAction=["skin-click"]
        MouseScrollUpAction=["skin-scroll"]

        [MeterBack]
        Meter=Image
        W=100
        H=100
        SolidColor=0,0,0,1
        LeftMouseDoubleClickAction=["back-double"]
        MouseOverAction=["back-over"]
        MouseLeaveAction=["back-leave"]

        [MeterButton]
        Meter=Image
        X=10
        Y=10
        W=20
        H=20
        SolidColor=255,0,0
        LeftMouseUpAction=["button-up"]
        ToolTipText=Press me
    """)
    skin.mouse("move", 15, 15)
    assert host.commands == ["back-over"]
    assert host.pointer == ("pointer", "Press me")
    assert skin.mouse("up", 15, 15)
    assert skin.mouse("double", 50, 50)  # no LeftMouseDownAction anywhere: double-click action
    skin.mouse("up", 50, 50)  # falls through to [Rainmeter]
    skin.mouse("scrollup", 50, 50)
    skin.mouse("leave")
    assert host.commands == ["back-over", "button-up", "back-double", "skin-click", "skin-scroll", "back-leave"]


def test_input_rects_follow_meters(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterA]
        Meter=Image
        X=5
        Y=5
        W=10
        H=10

        [MeterHidden]
        Meter=Image
        X=50
        W=10
        H=10
        Hidden=1
    """)
    assert skin.input_rects() == [(5, 5, 10, 10)]
    assert (skin.width, skin.height) == (15, 15)
