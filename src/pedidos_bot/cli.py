"""Punto de entrada de línea de comandos. Los subcomandos llegan en hitos posteriores."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from pedidos_bot import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pedidos-bot",
        description="Asistente conversacional de pedidos para restaurantes.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
