"""Regressions found by loading popular suites with tools/suitecheck.py."""

from pathlib import Path

import pytest

from helpers import make_skin
from raintux.measures.plugins import files


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
