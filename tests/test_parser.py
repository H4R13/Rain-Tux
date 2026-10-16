from pathlib import Path

from raintux.core.ini_writer import write_key_value
from raintux.core.skin_parser import parse_skin_ini, substitute_variables


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


def test_sections_measures_meters_and_styles(tmp_path: Path) -> None:
    ini = _write(
        tmp_path / "S" / "s.ini",
        "garbage line without equals\n"
        "[Rainmeter]\nUpdate=500\n\n[Variables]\nPad=12\n\n"
        "[MeasureCPU]\nMeasure=CPU\n\n[OldStyle]\nType=Time\n\n"
        "[StyleA]\nFontSize=9\n\n[MeterA]\nMeter=String\nText=\"quoted\"\nFormula=a % b ; not a comment\n",
    )
    p = parse_skin_ini(ini)
    assert p.rainmeter == {"update": "500"}
    assert list(p.measures) == ["MeasureCPU", "OldStyle"]
    assert [n for n, _ in p.meters] == ["MeterA"]
    meter = dict(p.meters)["MeterA"]
    assert meter["text"] == "quoted"  # one pair of enclosing quotes is stripped
    assert meter["formula"] == "a % b ; not a comment"  # no inline comments in Rainmeter
    assert "StyleA" in p.sections


def test_include_in_place_and_relative(tmp_path: Path) -> None:
    _write(tmp_path / "S" / "lib" / "a.inc", "[Variables]\nA=1\nB=from-include\n@Include=b.inc\n")
    _write(tmp_path / "S" / "lib" / "b.inc", "[Variables]\nC=3\n\n[MeasureX]\nMeasure=Calc\nFormula=1\n")
    ini = _write(
        tmp_path / "S" / "s.ini",
        "[Variables]\nB=before\nDir=lib\n@Include=#Dir#/a.inc\nA=after\n",
    )
    p = parse_skin_ini(ini)
    # Keys after the @Include override it; the include overrides keys before it.
    assert p.variables == {"b": "from-include", "dir": "lib", "a": "after", "c": "3"}
    assert "MeasureX" in p.measures


def test_include_cycle_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path / "S" / "a.inc", "[Variables]\nA=1\n@Include=s.ini\n")
    ini = _write(tmp_path / "S" / "s.ini", "[Variables]\n@Include=a.inc\nX=2\n")
    assert parse_skin_ini(ini).variables == {"a": "1", "x": "2"}


def test_utf16_skin(tmp_path: Path) -> None:
    ini = _write(tmp_path / "S" / "s.ini", "﻿[Variables]\nName=Grüße ✓\n", encoding="utf-16-le")
    assert parse_skin_ini(ini).variables["name"] == "Grüße ✓"


def test_backslashes_kept_for_regex(tmp_path: Path) -> None:
    ini = _write(tmp_path / "S" / "s.ini", "[M]\nMeasure=String\nRegExp=(?siU)<b>(\\d+)</b>\n")
    assert parse_skin_ini(ini).measures["M"]["regexp"] == "(?siU)<b>(\\d+)</b>"


def test_substitute_variables(tmp_path: Path) -> None:
    v = {"A": "#B#!", "b": "hi"}
    assert substitute_variables("#a# #Unknown# #*a*#", v, tmp_path) == "hi! #Unknown# #a#"
    assert substitute_variables("#CURRENTPATH#x.png", {}, tmp_path) == f"{tmp_path}/x.png"
    assert substitute_variables("#CURRENTPATH#", {"CURRENTPATH": "nope"}, tmp_path) == f"{tmp_path}/"


def test_write_key_value_keeps_layout(tmp_path: Path) -> None:
    f = _write(tmp_path / "v.inc", "; comment\r\n[Variables]\r\nColor=1,2,3\r\n\r\n[Other]\r\nX=1\r\n")
    assert write_key_value(f, "Variables", "color", "9,9,9")
    assert write_key_value(f, "Variables", "New", "yes")
    assert write_key_value(f, "Fresh", "K", "v")
    text = f.read_bytes().decode()
    assert text == "; comment\r\n[Variables]\r\nColor=9,9,9\r\nNew=yes\r\n\r\n[Other]\r\nX=1\r\n\r\n[Fresh]\r\nK=v\r\n"


def test_write_key_value_utf16_and_suffix_guard(tmp_path: Path) -> None:
    f = _write(tmp_path / "s.ini", "﻿[A]\nB=1\n", encoding="utf-16-le")
    write_key_value(f, "A", "B", "ü")
    assert f.read_bytes().startswith(b"\xff\xfe")
    assert "B=ü" in f.read_bytes().decode("utf-16")
    assert not write_key_value(tmp_path / "evil.sh", "A", "B", "1")
