from pathlib import Path

import pytest

from helpers import make_skin
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
        ("(2*3)", 6),
        ("(2*3)R", 106),
        ("bogus", 7),  # unparseable -> fallback
    ],
)
def test_parse_coord(value: str, expected: float) -> None:
    assert parse_coord(value, prev_pos=40, prev_end=100, fallback=7) == expected


def test_relative_positions_formulas_and_padding(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [Variables]
        Pad=10

        [MeterA]
        Meter=Image
        X=#Pad#
        Y=20
        W=50
        H=10
        Padding=1,2,3,4

        [MeterB]
        Meter=Image
        X=5R
        Y=2r
        W=(#Pad#*3)
        H=8

        [MeterC]
        Meter=Image
        X=0r
        Y=4R
        W=1
        H=1
    """)
    a, b, c = skin.meter("MeterA"), skin.meter("MeterB"), skin.meter("MeterC")
    assert a.box() == (10, 20, 54, 16)  # padding adds to the size
    assert (b.x, b.y, b.w) == (69, 22, 30)
    assert (c.x, c.y) == (69, 34)


def test_meter_style_precedence(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [StyleBase]
        W=10
        H=10
        X=1

        [StyleWide]
        W=40

        [MeterA]
        Meter=Image
        MeterStyle=StyleBase | StyleWide
        X=7
    """)
    a = skin.meter("MeterA")
    assert (a.x, a.w, a.h) == (7, 40, 10)  # own option > later style > earlier style


@pytest.mark.gtk
def test_string_alignment_moves_the_box(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterRight]
        Meter=String
        X=200
        Y=100
        W=80
        H=20
        StringAlign=RightBottom
        Text=hi

        [MeterCenter]
        Meter=String
        X=200
        Y=100
        W=80
        H=20
        StringAlign=CenterCenter
        Text=hi
    """)
    assert skin.meter("MeterRight").box() == (120, 80, 80, 20)
    assert skin.meter("MeterCenter").box() == (160, 90, 80, 20)


def test_default_sizes_and_unparseable_values(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterBar]
        Meter=Bar
        W=(1+)
        H=abc

        [MeterImageMissing]
        Meter=Image
        ImageName=does-not-exist.png
    """)
    bar = skin.meter("MeterBar")
    assert (bar.w, bar.h) == (0, 0)
    assert skin.meter("MeterImageMissing").w == 0


def test_container_offsets_children(tmp_path: Path) -> None:
    skin, _ = make_skin(tmp_path, """
        [MeterBox]
        Meter=Image
        X=100
        Y=50
        W=40
        H=40
        SolidColor=255,255,255

        [MeterInside]
        Meter=Image
        Container=MeterBox
        X=5
        Y=5
        W=100
        H=100
        SolidColor=0,255,0
    """)
    inside = skin.meter("MeterInside")
    assert (inside.x, inside.y) == (105, 55)
    assert skin.meter("MeterBox").children == [inside]
    assert inside.hit_test(110, 60) and not inside.hit_test(150, 60)  # clipped by the container
    assert (skin.width, skin.height) == (140, 90)  # contained meters don't grow the window


def test_image_meter_scaling(tmp_path: Path) -> None:
    pil = pytest.importorskip("PIL.Image")
    img_dir = tmp_path / "Skins" / "Test"
    img_dir.mkdir(parents=True)
    im = pil.new("RGBA", (40, 20), (255, 0, 0, 255))
    im.putpixel((0, 0), (0, 0, 0, 0))
    im.save(img_dir / "pic.png")
    skin, _ = make_skin(tmp_path, """
        [MeterNatural]
        Meter=Image
        ImageName=pic.png

        [MeterWidthOnly]
        Meter=Image
        ImageName=pic
        W=80

        [MeterCrop]
        Meter=Image
        ImageName=pic.png
        ImageCrop=10,0,10,10,2
    """)
    assert (skin.meter("MeterNatural").w, skin.meter("MeterNatural").h) == (40, 20)
    assert (skin.meter("MeterWidthOnly").w, skin.meter("MeterWidthOnly").h) == (80, 40)  # aspect kept
    assert skin.meter("MeterCrop").src == (50, 0, 10, 10)  # origin 2 = top right
    nat = skin.meter("MeterNatural")
    # Rainmeter hit-tests Image meters by their rectangle; fully transparent window pixels
    # click through at the window (input region), not per meter.
    assert nat.hit_test(0, 0) and nat.hit_test(5, 5)
    assert not nat.hit_test(45, 5)
