"""Masking with Colombian phones and addresses. All of this data is made up."""

import pytest

from pedidos_bot.observability.masking import hash_customer, mask_text


@pytest.mark.parametrize(
    "phone",
    [
        "3001234567",
        "300 123 4567",
        "300-123-4567",
        "+57 300 123 4567",
        "+573001234567",
        "573001234567",
        "601 234 5678",
        "6012345678",
    ],
)
def test_phones_are_masked(phone: str) -> None:
    assert mask_text(f"mi número es {phone} gracias") == "mi número es [phone] gracias"


@pytest.mark.parametrize(
    "address",
    [
        "calle 10 # 5-20",
        "Calle 10 #5-20",
        "cra 7 # 45-10 apto 301",
        "Cra. 7 No. 45-10",
        "cl 80 12 34",
        "carrera 15a # 93-07, torre 2",
        "av 68 # 22-10",
        "Diagonal 40 # 12b-30 casa 4",
        "kr 30 # 1-05 interior 2",
    ],
)
def test_addresses_are_masked(address: str) -> None:
    assert mask_text(f"llevalo a {address}") == "llevalo a [address]"


@pytest.mark.parametrize(
    "text",
    [
        "2 sencillas y una coca",
        "con 50.000",
        "50000",
        "12345",
        "una calle sin salida",
        "pedido 30012345678901",
    ],
)
def test_other_text_is_left_alone(text: str) -> None:
    assert mask_text(text) == text


def test_customer_hash_is_stable_and_depends_on_the_salt() -> None:
    first = hash_customer("573001234567", "salt-a")
    assert first == hash_customer("573001234567", "salt-a")
    assert first != hash_customer("573001234567", "salt-b")
    assert first != hash_customer("573001234568", "salt-a")
    assert "3001234567" not in first
