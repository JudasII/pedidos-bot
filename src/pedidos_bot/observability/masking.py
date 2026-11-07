"""Keeps customer data out of logs. Patterns follow Colombian phones and addresses."""

from __future__ import annotations

import hashlib
import hmac
import re

_PHONE = re.compile(
    r"(?<![\d])(?:\+?57[\s.-]?)?(?:3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}|60\d[\s.-]?\d{3}[\s.-]?\d{4})(?!\d)"
)
_ADDRESS = re.compile(
    r"\b(?:calle|cll?|carrera|cra|cr|kr|avenida|av|diagonal|dg|transversal|tv)\b\.?\s*\d+\w*"
    r"(?:\s*(?:#|no\.?|n°)?\s*\d+\w*(?:\s*-\s*\d+\w*)?)*"
    r"(?:\s*,?\s*(?:apto|apartamento|torre|casa|piso|interior)\.?\s*\w+)*",
    re.IGNORECASE,
)


def mask_text(text: str) -> str:
    return _ADDRESS.sub("[address]", _PHONE.sub("[phone]", text))


def hash_customer(customer_ref: str, salt: str) -> str:
    """A stable identifier for logs that can't be turned back into the customer's."""
    digest = hmac.new(salt.encode(), customer_ref.encode(), hashlib.sha256)
    return digest.hexdigest()[:16]
