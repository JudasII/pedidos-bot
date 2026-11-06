"""Money helpers. Amounts are integers in whole currency units (COP has no cents in practice)."""

from __future__ import annotations


def format_cop(amount: int) -> str:
    """Format an amount the way Colombians write prices: `$18.000`."""
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,}".replace(",", ".")
