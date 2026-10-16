from pathlib import Path

import pytest

from raintux.core.meter_renderer import compute_layout
from raintux.core.skin_parser import parse_skin_ini
from raintux.meters.base_meter import parse_coord


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("15", 15),
        ("", 0),
        ("5R", 105),  # previous right/bottom edge
        ("5B", 105),
        ("R", 100),
        ("5r", 45),  # previous X/Y
        ("-5b", 35),
        ("r", 40),
        ("(1+2)", 7),  # formulas not evaluated yet -> fallback
        ("(1+2)R", 7),
    ],
)
def test_parse_coord(value: str, expected: int) -> None:
    assert parse_coord(value, prev_pos=40, prev_end=100, fallback=7) == expected


def _layout(tmp_path: Path, body: str):
    ini = tmp_path / "skin.ini"
    ini.write_text("[Rainmeter]\nUpdate=1000\n[Variables]\nPad=10\n" + body, encoding="utf-8")
    return {l.name: l for l in compute_layout(parse_skin_ini(ini))}


def test_defaults_by_meter_type(tmp_path: Path) -> None:
    lays = _layout(
        tmp_path,
        "[MeterImg]\nMeter=Image\nImageName=x.png\n"
        "[MeterBar]\nMeter=Bar\n"
        "[MeterStr]\nMeter=String\n",
    )
    assert (lays["MeterImg"].w, lays["MeterImg"].h) == (0, 0)
    assert (lays["MeterBar"].w, lays["MeterBar"].h) == (150, 14)
    assert (lays["MeterStr"].w, lays["MeterStr"].h) == (120, 30)


def test_relative_positions(tmp_path: Path) -> None:
    lays = _layout(
        tmp_path,
        "[MeterA]\nMeter=String\nX=#Pad#\nY=20\nW=50\nH=10\n"
        "[MeterB]\nMeter=String\nX=5R\nY=2r\nW=30\nH=8\n"
        "[MeterC]\nMeter=String\nX=0r\nY=4B\n",
    )
    assert (lays["MeterA"].x, lays["MeterA"].y) == (10, 20)
    assert (lays["MeterB"].x, lays["MeterB"].y) == (65, 22)
    assert (lays["MeterC"].x, lays["MeterC"].y) == (65, 34)


def test_unparseable_size_does_not_fail_layout(tmp_path: Path) -> None:
    lays = _layout(tmp_path, "[MeterBar]\nMeter=Bar\nW=(#Pad#*20)\nH=abc\n[MeterImg]\nMeter=Image\nW=(1+1)\n")
    assert (lays["MeterBar"].w, lays["MeterBar"].h) == (150, 14)
    assert lays["MeterImg"].w == 0
