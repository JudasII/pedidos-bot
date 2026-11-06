"""Per-restaurant configuration: schema and loading."""

from pedidos_bot.config.loader import ConfigError, load_config, load_restaurant
from pedidos_bot.config.schema import RestaurantConfig

__all__ = ["ConfigError", "RestaurantConfig", "load_config", "load_restaurant"]
