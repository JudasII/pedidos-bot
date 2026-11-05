"""Load and validate restaurant configuration files."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from pedidos_bot.config.schema import RestaurantConfig

DEFAULT_CONFIG_DIR = Path("config/restaurants")


class ConfigError(Exception):
    """A restaurant configuration could not be loaded. The message is human-readable."""


def load_config(path: str | Path) -> RestaurantConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: file not found") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    try:
        return RestaurantConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(path, exc)) from exc


def load_restaurant(
    restaurant_id: str, config_dir: str | Path = DEFAULT_CONFIG_DIR
) -> RestaurantConfig:
    config = load_config(Path(config_dir) / f"{restaurant_id}.yaml")
    if config.restaurant.id != restaurant_id:
        raise ConfigError(
            f"{restaurant_id}.yaml declares restaurant.id '{config.restaurant.id}'; "
            "the file name must match the id"
        )
    return config


def format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}: {exc.error_count()} configuration error(s)"]
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        message = error["msg"].removeprefix("Value error, ")
        lines.append(f"  - {location}: {message}")
    return "\n".join(lines)


def resolve_staff_chat_id(config: RestaurantConfig, env: Mapping[str, str] | None = None) -> str:
    """Return the staff chat id from the environment variable named in the config."""
    env = os.environ if env is None else env
    var = config.restaurant.notify.chat_id_env
    value = env.get(var, "").strip()
    if not value:
        raise ConfigError(
            f"environment variable {var} (staff chat for '{config.restaurant.id}') is not set"
        )
    return value


def config_json_schema() -> dict[str, Any]:
    schema = RestaurantConfig.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "pedidos-bot restaurant configuration"
    return schema


def config_json_schema_text() -> str:
    return json.dumps(config_json_schema(), indent=2, ensure_ascii=False) + "\n"
