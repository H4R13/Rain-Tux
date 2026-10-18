"""Regressions found by loading popular suites with tools/suitecheck.py."""

from pathlib import Path

import pytest

from helpers import make_skin
from raintux.measures.plugins import files, media


@pytest.fixture
def fake_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A trash with two items under tmp_path; emptying it is recorded, never done."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for name in ("a.txt", "b.txt"):
        p = tmp_path / "data" / "Trash" / "files" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    emptied: list[str] = []
    monkeypatch.setattr(files, "empty_trash", lambda: emptied.append("emptied"))
    return emptied


BIN = """
    [MeasureBin]
    Measure=Plugin
    Plugin=RecycleManager
    RecycleType=Count
"""


def test_empty_bin_asks_first(tmp_path: Path, fake_trash: list[str]) -> None:
    skin, host = make_skin(tmp_path, BIN)
    skin.execute('[!CommandMeasure MeasureBin "EmptyBin"]')
    assert fake_trash == []  # nothing deleted until the user says yes
    (title, message, answer), = host.confirms
    assert "2 items" in message
    answer(False)
    assert fake_trash == []
    skin.execute('[!CommandMeasure MeasureBin "EmptyBin"]')
    host.confirms[-1][2](True)
    assert fake_trash == ["emptied"]


def test_empty_bin_silent_does_not_ask(tmp_path: Path, fake_trash: list[str]) -> None:
    skin, host = make_skin(tmp_path, BIN)
    skin.execute("[!RecycleManager:EmptyBinSilent MeasureBin]")
    assert fake_trash == ["emptied"] and host.confirms == []


def test_empty_bin_on_an_empty_trash_asks_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nothing"))
    monkeypatch.setattr(files, "empty_trash", lambda: pytest.fail("emptied"))
    skin, host = make_skin(tmp_path, BIN)
    skin.execute('[!CommandMeasure MeasureBin "EmptyBin"]')
    assert host.confirms == []


class FakeMpris:
    """One MPRIS player, as Mpris.get() returns it; Set calls are recorded."""

    class GLib:
        @staticmethod
        def Variant(sig: str, value: object) -> object:  # noqa: N802 - GLib's name
            return value

    def __init__(self, props: dict, name: str = "org.mpris.MediaPlayer2.spotify") -> None:
        self.names, self.props, self.calls = [name], props, []

    def pick(self, preferred: str) -> str:
        return self.names[0]

    def prop(self, name: str, key: str) -> object:
        return self.props.get(key)

    def position_us(self, name: str) -> int:
        return 75_000_000

    def call(self, name: str, method: str, sig: str | None = None, args: tuple = ()) -> None:
        self.calls.append((method, args))

    def set_prop(self, name: str, key: str, value: object) -> None:
        self.calls.append(("Set", key, value))
        self.props[key] = value


@pytest.fixture
def player(monkeypatch: pytest.MonkeyPatch) -> FakeMpris:
    fake = FakeMpris({"PlaybackStatus": "Paused", "LoopStatus": "Playlist", "Shuffle": False, "Volume": 0.5,
                      "Metadata": {"xesam:title": "Song", "xesam:artist": ["Band"], "xesam:album": "Record",
                                   "mpris:length": 300_000_000, "mpris:artUrl": "file:///tmp/cover.png"}})
    monkeypatch.setattr(media.Mpris, "get", classmethod(lambda cls: fake))
    return fake


def test_webnowplaying_reads_the_mpris_player(tmp_path: Path, player: FakeMpris) -> None:
    """Summit's Music Info, ModernGadgets' AudioMeter and Monstercat use Plugin=WebNowPlaying."""
    skin, _ = make_skin(tmp_path, """
        [MeasureTitle]
        Measure=Plugin
        Plugin=WebNowPlaying
        PlayerType=Title

        [MeasureArtist]
        Measure=Plugin
        Plugin=WebNowPlaying.dll
        PlayerType=Artist

        [MeasureState]
        Measure=Plugin
        Plugin=WebNowPlaying
        PlayerType=State

        [MeasureRepeat]
        Measure=Plugin
        Plugin=WebNowPlaying
        PlayerType=Repeat

        [MeasureProgress]
        Measure=Plugin
        Plugin=WebNowPlaying
        PlayerType=Progress

        [MeasurePosition]
        Measure=Plugin
        Plugin=GPMDPPlugin
        PlayerInfo=Position
    """)
    skin.update()
    assert skin.measure("MeasureTitle").string_value() == "Song"
    assert skin.measure("MeasureArtist").string_value() == "Band"
    assert skin.measure("MeasureState").value == 2  # paused
    assert skin.measure("MeasureRepeat").value == 2  # repeat all
    assert skin.measure("MeasureProgress").value == 25
    assert skin.measure("MeasurePosition").string_value() == "1:15"
    skin.execute('[!CommandMeasure MeasureState "PlayPause"][!CommandMeasure MeasureState "Repeat"]')
    assert player.calls == [("PlayPause", ()), ("Set", "LoopStatus", "Track")]


def test_spotify_plugin_types(tmp_path: Path, player: FakeMpris) -> None:
    """Win10 Widgets, SysDash, Lano and Elementary use SpotifyPlugin's Type names and formats."""
    skin, _ = make_skin(tmp_path, """
        [MeasureTrack]
        Measure=Plugin
        Plugin=SpotifyPlugin
        Type=TrackName

        [MeasureLength]
        Measure=Plugin
        Plugin=SpotifyPlugin
        Type=Length

        [MeasureProgress]
        Measure=Plugin
        Plugin=SpotifyPlugin
        Type=Progress

        [MeasurePlaying]
        Measure=Plugin
        Plugin=SpotifyPlugin
        Type=Playing

        [MeasureCover]
        Measure=Plugin
        Plugin=SpotifyPlugin
        Type=AlbumArt
        Res=300
    """)
    skin.update()
    assert skin.measure("MeasureTrack").string_value() == "Song"
    assert skin.measure("MeasureLength").string_value() == "05:00"  # skins strip the leading 0 themselves
    assert skin.measure("MeasureProgress").value == pytest.approx(0.25)
    assert skin.measure("MeasurePlaying").value == 0  # paused is "not playing"
    assert skin.measure("MeasureCover").string_value() == "/tmp/cover.png"


def test_variable_names_with_spaces(tmp_path: Path) -> None:
    """Mond's language files define ``Recycle Bin=Recycle Bin`` and its skin shows ``#Recycle Bin#``."""
    skin, _ = make_skin(tmp_path, """
        [Variables]
        Recycle Bin=Papierkorb
        Files=Dateien

        [MeterText]
        Meter=String
        Text="#Recycle Bin# - #Files# 3"

        [MeterHash]
        Meter=String
        Text=Track #1 of #Files#, #*Files*#
    """)
    assert skin.meter("MeterText").text == "Papierkorb - Dateien 3"
    # "#1 of #" names no variable, so its closing "#" starts "#Files#" (Rainmeter's scan).
    assert skin.meter("MeterHash").text == "Track #1 of Dateien, #Files#"


def test_regexp_substitute_like_rainmeter(tmp_path: Path) -> None:
    r"""Monterey's Notes use Substitute="\r\n":"[*\13*][*\10*]" with no groups: Python's re.sub
    refused "\13". Rainmeter only fills \N of groups that matched, never takes an empty match on
    non-empty text, and goes on after what it inserted."""
    skin, _ = make_skin(tmp_path, r"""
        [MeasureNotes]
        Measure=String
        String=one#CRLF#two
        RegExpSubstitute=1
        Substitute="\n":"[*\13*]","^":"start ","(\w+)":"<\1>","(x)?y":"[\1]"

        [MeasureEmpty]
        Measure=String
        String=
        RegExpSubstitute=1
        Substitute="":"none"
    """)
    skin.update()
    # [*\13*] is read as [\13] (an escaped section variable); "(\w+)" then wraps "13" as well.
    assert skin.measure("MeasureNotes").string_value() == r"<one>[\<13>]<two>"
    assert skin.measure("MeasureEmpty").string_value() == "none"


def test_formula_errors_like_rainmeter(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Sonder's Formula=[@CurrentTemperature] is empty until the weather arrives: Rainmeter logs the
    error once, keeps the Calc's value, and an IfCondition fires only on exactly 1 or 0."""
    skin, _ = make_skin(tmp_path, """
        [Variables]
        Temp=21
        Hits=0
        Fives=0

        [MeasureTemp]
        Measure=Calc
        Formula=#Temp#
        DynamicVariables=1

        [MeasureFive]
        Measure=Calc
        Formula=5
        IfCondition=MeasureFive
        IfTrueAction=[!SetVariable Fives 1]
        IfFalseAction=[!SetVariable Fives -1]
        IfCondition2=#Temp# > 20
        IfTrueAction2=[!SetVariable Hits (#Hits#+1)]
        DynamicVariables=1
    """)
    skin.update()
    assert skin.measure("MeasureTemp").value == 21 and skin.get_variable("Hits") == "1"
    skin.execute("[!SetVariable Temp \"\"]")
    for _ in range(5):
        skin.update()
    assert skin.measure("MeasureTemp").value == 21  # kept
    assert sum("Formula=" in r.message for r in caplog.records) == 1
    assert sum("IfCondition2" in r.message for r in caplog.records) == 1
    assert skin.get_variable("Fives") == "0"  # 5 is neither true nor false


def test_pcre_conditional_on_an_assertion() -> None:
    """Sonder's ISS crew list repeats (?(?=.*"name":).*"name": "(.*)") 16 times: match one more
    name if there is one. Python's re only takes a group number as the condition."""
    from raintux.core.pcre import compile_pcre

    item = '(?(?=.*"name":).*"name": "(.*)")'
    m = compile_pcre("(?siU)" + item * 3).search('[{"name": "Ann", "x": 1}, {"name": "Bob"}]')
    assert m is not None and m.groups() == ("Ann", "Bob", None)
    assert compile_pcre(r"^(?(?!a)b|c)$").match("c") is None
    assert compile_pcre(r"^(?(?!a)b|c)$").match("b") is not None
    assert compile_pcre(r"(?(?=(x))x|y)").groups == 1  # the negated copy captures nothing


def test_config_active_and_sys_color(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Monterey's settings ask ConfigActive which widgets are loaded; ModernGadgets' LoadSkin.lua
    calls [&Measure:IsActive(Config)]; Monstercat colours itself with SysColor's DWM_COLOR."""
    from raintux.compat import windows

    monkeypatch.setattr(windows, "accent_rgb", lambda: (10, 20, 30))
    skin, host = make_skin(tmp_path, r"""
        [MeasureMe]
        Measure=Plugin
        Plugin=ConfigActive
        ConfigName=Test

        [MeasureOther]
        Measure=Plugin
        Plugin=ConfigActive.dll
        ConfigName=Suite\Clock

        [MeasureAccent]
        Measure=Plugin
        Plugin=SysColor
        ColorType=DWM_COLOR
        DisplayType=RGB

        [MeasureBlur]
        Measure=Plugin
        Plugin=FrostedGlass
        Type=Blur
    """)
    skin.update()
    assert (skin.measure("MeasureMe").value, skin.measure("MeasureOther").value) == (1, -1)
    assert skin.resolve("[&MeasureMe:IsActive(Test)] [&MeasureMe:ConfigVariantName('Test')]",
                        section_vars=True) == "1 Test.ini"
    assert skin.resolve("[&MeasureMe:IsActive(Suite\\Clock)]", section_vars=True) == "-1"
    assert skin.measure("MeasureAccent").string_value() == "10,20,30"
    assert type(skin.measure("MeasureBlur")).__name__ == "NoEffectPlugin"


def test_old_execute_bang(tmp_path: Path) -> None:
    """Simplicity Circles (Rainmeter 1.x era): !Execute [!SetVariable "playertxt" "closed"] [!Redraw]."""
    skin, _ = make_skin(tmp_path, """
        [Variables]
        playertxt=open
        b=0

        [MeasureX]
        Measure=Calc
        Formula=1
        IfAboveValue=0
        IfAboveAction=!Execute [!SetVariable "playertxt" "closed"] [!SetVariable b 2]
    """)
    skin.update()
    assert (skin.get_variable("playertxt"), skin.get_variable("b")) == ("closed", "2")


def test_disk_performance_counters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ModernGadgets' disks showed 100% activity: UsageMonitor Category=LogicalDisk was answered with
    process CPU. Win10 Widgets' PerfMon "% Disk Time" read 0. Both now come from /proc/diskstats."""
    from types import SimpleNamespace

    from raintux.measures.plugins import system

    clock = [100.0]
    io = SimpleNamespace(busy_time=0, read_bytes=0, write_bytes=0, read_time=0, write_time=0,
                         read_count=0, write_count=0)
    monkeypatch.setattr(system.time, "monotonic", lambda: clock[0])
    sample = lambda: SimpleNamespace(**vars(io))  # noqa: E731 - psutil returns a new tuple each time
    monkeypatch.setattr(system.psutil, "disk_io_counters",
                        lambda perdisk=False: {"sda1": sample()} if perdisk else sample())
    monkeypatch.setattr(system, "_disk_device", lambda drive: "sda1")
    skin, _ = make_skin(tmp_path, """
        [MeasureIdle]
        Measure=Plugin
        Plugin=UsageMonitor
        Category=LogicalDisk
        Counter=% Idle Time
        Name=C:

        [MeasureRead]
        Measure=Plugin
        Plugin=UsageMonitor
        Category=LogicalDisk
        Counter=Disk Read Bytes/sec
        Name=C:

        [MeasureDisk]
        Measure=Plugin
        Plugin=PerfMon
        PerfMonObject=LogicalDisk
        PerfMonCounter="% Disk Time"
        PerfMonInstance=_Total
    """)
    skin.update()
    clock[0] += 2.0
    io.busy_time, io.read_bytes = 500, 4096  # busy 500 ms of 2 s
    skin.update()
    assert skin.measure("MeasureIdle").value == pytest.approx(75.0)
    assert skin.measure("MeasureRead").value == pytest.approx(2048.0)
    assert skin.measure("MeasureDisk").value == pytest.approx(25.0)


def test_write_key_value_into_rainmeter_ini(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skins set their own window options with !WriteKeyValue into #SETTINGSPATH#Rainmeter.ini
    (Win10 Widgets, ModernGadgets); RainTux keeps them per config for the next refresh."""
    from raintux.core.state import AppState, apply_rainmeter_ini_key

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    skin, host = make_skin(tmp_path, r"""
        [Rainmeter]
        OnRefreshAction=[!WriteKeyValue "#CURRENTCONFIG#" AlwaysOnTop -2 "#SETTINGSPATH#Rainmeter.ini"][!WriteKeyValue Rainmeter DisableDragging 1 "#SETTINGSPATH#Rainmeter.ini"]
    """)
    written: list[tuple[str, str, str]] = []
    host.write_setting = lambda *a: written.append(a)  # type: ignore[attr-defined]
    skin.execute(skin.rm.get("onrefreshaction"))
    assert written == [("Test", "AlwaysOnTop", "-2"), ("Rainmeter", "DisableDragging", "1")]
    assert not (tmp_path / "config" / "raintux" / "Rainmeter.ini").exists()
    state = AppState()
    assert apply_rainmeter_ini_key(state, "Test", "AlwaysOnTop", "-2")
    assert apply_rainmeter_ini_key(state, "Test", "Draggable", "0")
    assert not apply_rainmeter_ini_key(state, "Rainmeter", "DisableDragging", "1")
    assert (state.config("Test").window.z, state.config("Test").window.draggable) == (-2, False)


@pytest.mark.parametrize("new_on_top", [True, False])
@pytest.mark.parametrize("current", ["abcd", "dcba", "badc", "acbd", "dabc", "bcda"])
def test_restack_plan_orders_a_layer(current: str, new_on_top: bool) -> None:
    """Sonder's world map (AlwaysOnTop=-2) drew over its Settings (0, LoadOrder=1): within a layer
    the compositor stacks by mapping order (newest on top, or at the bottom on Sway)."""
    from raintux.core.stacking import restack_plan

    stack = list(current)  # bottom first; the wanted order is a, b, c, d
    plan = restack_plan(stack, rank=str, new_on_top=new_on_top)
    for w in plan:  # what re-mapping does to the compositor's stack
        stack.remove(w)
        stack.append(w) if new_on_top else stack.insert(0, w)
    assert stack == list("abcd")
    if current == "abcd":
        assert plan == []
    if current == "dabc":  # only the window out of place is re-mapped where new ones go on top
        assert plan == (["d"] if new_on_top else ["c", "b", "a"])


@pytest.mark.parametrize(("z", "layer"), [(-2, "BOTTOM"), (-1, "BOTTOM"), (0, "BOTTOM"), (1, "TOP"), (2, "OVERLAY")])
def test_on_desktop_skins_stay_above_the_wallpaper(z: int, layer: str) -> None:
    """Sonder's skins are AlwaysOnTop=-2; on the background layer swaybg covered them."""
    from raintux.core.position import layer_for_zpos

    assert layer_for_zpos(z) == layer


def test_registry_measure_reads_linux_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ModernGadgets reads the CPU name and the clock format from the registry."""
    from raintux.compat import windows

    monkeypatch.setattr(windows, "cpu_name", lambda: "Test CPU @ 3.00GHz")
    monkeypatch.setattr(windows, "_langinfo", lambda item, default: {"T_FMT": "%H:%M:%S", "D_FMT": "%d.%m.%Y"}[item])
    skin, _ = make_skin(tmp_path, r"""
        [MeasureCpuName]
        Measure=Registry
        RegHKey=HKEY_LOCAL_MACHINE
        RegKey=HARDWARE\DESCRIPTION\System\CentralProcessor\0
        RegValue=ProcessorNameString

        [MeasureShortTime]
        Measure=Registry
        RegHKey=HKEY_CURRENT_USER
        RegKey=Control Panel\International
        RegValue=sShortTime

        [MeasureShortDate]
        Measure=Registry
        RegKey=Control Panel\International
        RegValue=sShortDate

        [MeasureMissing]
        Measure=Registry
        RegKey=Software\Gadgets\NetMonitor
        RegValue=BytesPerSecIn
    """)
    skin.update()
    assert skin.measure("MeasureCpuName").string_value() == "Test CPU @ 3.00GHz"
    assert skin.measure("MeasureShortTime").string_value() == "HH:mm"
    assert skin.measure("MeasureShortDate").string_value() == "dd.MM.yyyy"
    assert skin.measure("MeasureMissing").value == 0


def test_reg_query_accent_palette(monkeypatch: pytest.MonkeyPatch) -> None:
    """Win10 Widgets colours its date with the accent palette from ``reg query`` (bytes 9-11)."""
    import re

    from raintux.compat import windows

    monkeypatch.setattr(windows, "accent_rgb", lambda: None)  # Windows' default blue
    code, out = windows.emulate([r"C:\Windows\System32\reg.exe", "query",
                                 r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Accent",
                                 "/v", "AccentPalette"])
    assert code == 0
    hexes = re.search(r"REG_BINARY\s*([0-9A-F]+)", out).group(1)
    assert len(hexes) == 64 and hexes[24:30] == "0078D7"  # the accent itself is the fourth colour
    code, out = windows.emulate(["reg", "query", r"HKCU\Software\Nothing\Here"])
    assert code == 1 and "unable to find" in out


def test_write_key_value_any_file_in_the_skins_folder(tmp_path: Path) -> None:
    """Fountain of Colors writes its settings into extension-less files ("Bands"); Rainmeter
    allows any existing file under Skins or the settings folder, and never creates one."""
    outside = tmp_path / "outside.txt"
    outside.write_text("[Variables]\nA=1\n")
    skin, _ = make_skin(tmp_path, r"""
        [Rainmeter]
        @Include=Bands

        [Variables]
        A=1
    """, files={"Test/Bands": "[Variables]\nBands=10\n"})
    skin.execute(r'[!WriteKeyValue Variables Bands 32 "#CURRENTPATH#Bands"]')
    assert "Bands=32" in (skin.root / "Bands").read_text()
    skin.execute(f'[!WriteKeyValue Variables A 2 "{outside}"]')
    assert outside.read_text() == "[Variables]\nA=1\n"  # outside the skins folder
    skin.execute(r'[!WriteKeyValue Variables A 2 "#CURRENTPATH#New.inc"]')
    assert not (skin.root / "New.inc").exists()


def test_include_rules(tmp_path: Path) -> None:
    """ConfigParser::ReadIniFile: empty @Include is skipped (FoC), [#Var] works in the path
    (Enigma), the first of duplicate keys / sections in a file wins, a later include overrides."""
    skin, _ = make_skin(tmp_path, r"""
        [Variables]
        Sheet=Dark
        Color=first
        Color=second
        Over=main
        @Include=
        @Include2=[#@]Styles\[#Sheet]\Options.inc

        [Variables]
        Color=third

        [MeterText]
        Meter=String
        Text=#Color# #Over# #Style#
    """, files={"Test/@Resources/Styles/Dark/Options.inc": "Over=ignored\n[Variables]\nOver=included\nStyle=dark\n"})
    assert skin.meter("MeterText").text == "first included dark"


@pytest.mark.gtk
def test_any_font_weight_draws(tmp_path: Path) -> None:
    """JaxCore and Expressive Windows use FontWeight=650 / 999; Pango.Weight(650) raised."""
    from raintux.meters.text import pango_weight

    skin, _ = make_skin(tmp_path, """
        [MeterText]
        Meter=String
        Text=Hello
        FontWeight=650
        InlineSetting=Weight | 999
    """)
    skin.update()
    assert skin.meter("MeterText").w > 0
    assert (int(pango_weight(650)), int(pango_weight(1)), int(pango_weight(999))) == (700, 100, 1000)


def test_disk_partitions_are_read_once_per_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """ModernGadgets' DisksMeter asked psutil for every mount (pathconf on each) per measure per update."""
    from raintux.measures import disk_measure as dm

    calls: list[bool] = []
    monkeypatch.setattr(dm.psutil, "disk_partitions", lambda all=False: calls.append(all) or [])
    monkeypatch.setattr(dm, "_PARTITIONS", {})
    for _ in range(50):
        dm.partitions(True)
        dm.partitions()
    assert calls == [True, False]


def test_run_command_answers_wmic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SysDash shows the GPU name from ``wmic path win32_VideoController get name``."""
    from raintux.compat import windows

    monkeypatch.setattr(windows, "gpu_names", lambda: ["Test GPU 9000"])
    skin, host = make_skin(tmp_path, """
        [MeasureGPUName]
        Measure=Plugin
        Plugin=RunCommand
        Parameter=wmic path win32_VideoController get name
        RegExpSubstitute=1
        Substitute="Name.*#CRLF#":"","#CRLF#.*":""
    """)
    skin.execute('[!CommandMeasure MeasureGPUName "Run"]')
    host.drain(timeout=5)
    skin.update()
    assert skin.measure("MeasureGPUName").string_value().strip() == "Test GPU 9000"
