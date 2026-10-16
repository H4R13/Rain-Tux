import math

import pytest

from raintux.core.formula import FormulaError, evaluate, parse_number


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("1+2*3", 7),
        ("(1+2)*3", 9),
        ("2**3**2", 512),
        ("-2**2", -4),
        ("10/0", 0),  # Rainmeter: division by zero is 0
        ("7%3", 1),
        ("1=1 ? 5 : 6", 5),
        ("0 ? 1 : 2 ? 3 : 4", 3),
        ("clamp(15,0,10)", 10),
        ("round(2.5)", 3),
        ("round(3.14159,2)", 3.14),
        ("0x10+0b11+0o7", 26),
        ("5 & 3", 1),
        ("5 | 3", 7),
        ("5 ^ 3", 6),
        ("1<<4", 16),
        ("!0 + ~0", 0),
        ("abs(-3)+min(2,1)+max(1,4)", 8),
        ("3 > 2 && 2 >= 2", 1),
        ("1 <> 2", 1),
        ("1 - -1", 2),
        ("trunc(-2.7) + frac(2.25)", -1.75),
        ("sgn(-4)", -1),
        ("Floor(Rad(180)*100)", 314),
    ],
)
def test_evaluate(src: str, expected: float) -> None:
    assert evaluate(src) == pytest.approx(expected)


def test_identifiers_resolve_through_callback() -> None:
    assert evaluate("MeasureCPU * 2 + pi", lambda n: 21 if n == "MeasureCPU" else None) == pytest.approx(42 + math.pi)
    with pytest.raises(FormulaError):
        evaluate("Missing + 1", lambda n: None)


@pytest.mark.parametrize("bad", ["1 +", "(1", "1 2", "", "foo(", "$"])
def test_syntax_errors(bad: str) -> None:
    with pytest.raises(FormulaError):
        evaluate(bad)


def test_parse_number() -> None:
    assert parse_number("(5+5)") == 10
    assert parse_number("12") == 12
    assert parse_number("12px") == 12  # leading number, like Rainmeter
    assert parse_number("abc", 7) == 7
    assert parse_number("5+5") == 5  # formulas need parentheses
    assert parse_number("(1+1)*2") == 0  # not wrapped as a whole: not a formula (Rainmeter reads 0)
