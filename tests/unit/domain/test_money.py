import pytest

from pedidos_bot.domain.money import format_cop


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(0, "$0"), (500, "$500"), (18000, "$18.000"), (1250000, "$1.250.000"), (-5000, "-$5.000")],
)
def test_format_cop(amount: int, expected: str) -> None:
    assert format_cop(amount) == expected
