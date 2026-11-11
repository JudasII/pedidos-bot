from datetime import timedelta

import pytest

from pedidos_bot.storage.base import retry_delay


@pytest.mark.parametrize(
    ("attempts", "seconds"),
    [(1, 2), (2, 4), (3, 8), (8, 256), (9, 300), (20, 300)],
)
def test_retries_double_up_to_a_limit(attempts: int, seconds: int) -> None:
    assert retry_delay(attempts) == timedelta(seconds=seconds)
