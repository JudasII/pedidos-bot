"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pedidos_bot import __version__
from pedidos_bot.channels.terminal import run_chat
from pedidos_bot.config.loader import (
    ConfigError,
    config_json_schema_text,
    load_config,
    load_restaurant,
)
from pedidos_bot.engine.actions import actions_json_schema
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.observability.logging import configure_logging


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

    chat = sub.add_parser("chat", help="chat with the bot in the terminal, with the fake LLM")
    chat.add_argument("--restaurant", required=True)
    chat.add_argument("--config-dir", type=Path, default=Path("config/restaurants"))

    nlu = sub.add_parser("nlu", help="LLM contract tools")
    nlu_sub = nlu.add_subparsers(dest="nlu_command", required=True)
    nlu_schema = nlu_sub.add_parser("schema", help="print the actions JSON Schema")
    nlu_schema.add_argument("--output", type=Path, help="write to a file instead of stdout")

    resolve = sub.add_parser("resolve", help="debug the menu resolver with customer wording")
    resolve.add_argument("--restaurant", required=True)
    resolve.add_argument("--config-dir", type=Path, default=Path("config/restaurants"))
    resolve.add_argument("--payment", action="store_true", help="resolve a payment method")
    resolve.add_argument("text")
    resolve.add_argument("modifiers", nargs="*")
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


def _schema(text: str, output: Path | None) -> int:
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    return 0


def _resolve(args: argparse.Namespace) -> int:
    try:
        resolver = MenuResolver(load_restaurant(args.restaurant, args.config_dir))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.payment:
        match = resolver.resolve_payment(args.text)
        print(f"payment: {match.status} {[(c.id, c.score) for c in match.candidates]}")
        return 0
    item = resolver.resolve_item(args.text, args.modifiers)
    product = item.product
    print(
        f"product: {product.status} via {product.method} "
        f"{[(c.id, c.score) for c in product.candidates]}"
    )
    for match in item.modifiers:
        print(
            f"modifier {match.query!r}: {match.status} "
            f"{[(c.group_id, c.id, c.score) for c in match.candidates]}"
        )
    return 0


def _chat(args: argparse.Namespace) -> int:
    try:
        config = load_restaurant(args.restaurant, args.config_dir)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    configure_logging("ERROR")  # turn logs would get in the way of the chat
    asyncio.run(run_chat(config))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config":
        if args.config_command == "validate":
            return _validate(args.paths)
        return _schema(config_json_schema_text(), args.output)
    if args.command == "nlu":
        text = json.dumps(actions_json_schema(), indent=2, ensure_ascii=False) + "\n"
        return _schema(text, args.output)
    if args.command == "chat":
        return _chat(args)
    if args.command == "resolve":
        return _resolve(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
