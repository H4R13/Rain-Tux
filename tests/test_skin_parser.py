from pathlib import Path

from raintux.core.event_bus import EventBus
from raintux.core.measure_manager import MeasureManager
from raintux.core.skin_parser import parse_skin_ini, substitute_variables


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_include_merges_sections_and_variables(tmp_path: Path) -> None:
    _write(tmp_path / "vars.inc", "[Variables]\nColor=255,0,0\n\n[mExtra]\nType=Time\n")
    ini = _write(
        tmp_path / "skin.ini",
        "[Rainmeter]\nUpdate=1000\n@Include=#CURRENTPATH#vars.inc\n\n[Variables]\nPad=12\n\n"
        "[mCPU]\nType=CPU\n\n[MeterA]\nMeter=String\nX=#Pad#\nFontColor=#Color#\n",
    )
    parsed = parse_skin_ini(ini)
    assert parsed.variables == {"pad": "12", "color": "255,0,0"}
    assert list(parsed.measures) == ["mCPU", "mExtra"]
    assert all(not k.startswith("@include") for k in parsed.rainmeter)
    name, section = parsed.meters[0]
    assert name == "MeterA"
    assert parsed.variable_text(section["fontcolor"]) == "255,0,0"
    assert parsed.variable_text(section["x"]) == "12"


def test_include_relative_to_including_file(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    _write(tmp_path / "lib" / "a.inc", "[Variables]\nA=1\n@Include=b.inc\n")
    _write(tmp_path / "lib" / "b.inc", "[Variables]\nB=2\n")
    ini = _write(tmp_path / "skin.ini", "[Rainmeter]\n@Include=lib/a.inc\n")
    parsed = parse_skin_ini(ini)
    assert parsed.variables == {"a": "1", "b": "2"}


def test_substitute_variables_recursive_and_builtins(tmp_path: Path) -> None:
    v = {"A": "#B#!", "b": "hi"}
    assert substitute_variables("#a# #Unknown#", v, tmp_path) == "hi! #Unknown#"
    assert substitute_variables("#CURRENTPATH#x.png", {}, tmp_path) == f"{tmp_path}/x.png"


def _intervals(tmp_path: Path, rainmeter: str, measure: str) -> int:
    ini = _write(tmp_path / "skin.ini", f"[Rainmeter]\n{rainmeter}\n\n[mX]\nType=CPU\n{measure}\n")
    parsed = parse_skin_ini(ini)
    mgr = MeasureManager(EventBus(), default_interval_ms=1000)
    return mgr._parse_interval(parsed.measures["mX"], parsed)


def test_update_key_is_honoured(tmp_path: Path) -> None:
    assert _intervals(tmp_path, "Update=250", "") == 250


def test_update_divider_uses_update_in_ms(tmp_path: Path) -> None:
    assert _intervals(tmp_path, "Update=500", "UpdateDivider=3") == 1500
    assert _intervals(tmp_path, "", "UpdateDivider=2") == 2000


def test_update_rate_overrides(tmp_path: Path) -> None:
    assert _intervals(tmp_path, "Update=500", "UpdateRate=20") == 50
