"""Small pure helpers of a turn. Whole turns are covered in tests/e2e."""

import pytest

from pedidos_bot.engine.turn import parse_amount


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("50000", 50000),
        ("50.000", 50000),
        ("$50.000", 50000),
        ("con 50 mil", 50000),
        ("100k", 100000),
        ("un billete de 20.000", 20000),
        ("exacto", None),
        ("", None),
    ],
)
def test_parse_amount(text: str, expected: int | None) -> None:
    assert parse_amount(text) == expected
