"""PostgreSQL fixtures. Each test gets an empty schema."""

import os
from collections.abc import AsyncIterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from pedidos_bot.storage.base import ConversationRepository
from pedidos_bot.storage.memory import InMemoryRepository
from pedidos_bot.storage.postgres import PostgresRepository

DEFAULT_URL = "postgresql://pedidos:pedidos@localhost:5433/pedidos_test"


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_URL)
    name = conninfo_to_dict(url)["dbname"]
    # This wipes the database, so it must never run against anything but a test one.
    assert str(name).endswith("_test"), f"refusing to wipe database {name!r}"
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(str(name))))
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    return url


@pytest.fixture(
    params=[pytest.param("memory"), pytest.param("postgres", marks=pytest.mark.postgres)]
)
async def repository(request: pytest.FixtureRequest) -> AsyncIterator[ConversationRepository]:
    """Every contract test runs against both implementations."""
    if request.param == "memory":
        yield InMemoryRepository()
        return
    url = request.getfixturevalue("database_url")
    async with PostgresRepository.open(url) as postgres:
        yield postgres
