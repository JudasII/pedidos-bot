"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pedidos_bot import __version__
from pedidos_bot.config.loader import ConfigError, config_json_schema_text, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pedidos-bot",
        description="Conversational ordering assistant for restaurants.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    config = sub.add_parser("config", help="restaurant configuration tools")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    validate = config_sub.add_parser("validate", help="validate restaurant YAML files")
    validate.add_argument("paths", nargs="+", type=Path)
    schema = config_sub.add_parser("schema", help="print the configuration JSON Schema")
    schema.add_argument("--output", type=Path, help="write to a file instead of stdout")
    return parser


def _validate(paths: Sequence[Path]) -> int:
    failed = 0
    for path in paths:
        try:
            config = load_config(path)
        except ConfigError as exc:
            failed += 1
            print(exc, file=sys.stderr)
        else:
            products = sum(1 for _ in config.menu.products())
            print(f"{path}: OK ({config.restaurant.id}, {products} products)")
    return 1 if failed else 0


def _schema(output: Path | None) -> int:
    text = config_json_schema_text()
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config":
        if args.config_command == "validate":
            return _validate(args.paths)
        return _schema(args.output)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
