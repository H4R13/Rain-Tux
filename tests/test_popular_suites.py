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
