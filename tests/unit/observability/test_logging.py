"""Where the logs go and what they look like."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from pedidos_bot.observability.logging import configure_logging


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    yield
    configure_logging()  # closes a log file left open by the test
    structlog.reset_defaults()


def test_logs_are_also_kept_in_a_plain_text_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_file = tmp_path / "logs" / "resto.log"
    configure_logging("INFO", log_file)
    structlog.get_logger().info("turn", message="una sencilla", state_after="ordering")
    [line] = log_file.read_text(encoding="utf-8").splitlines()
    assert line.endswith(
        "Z [info     ] turn                           message='una sencilla' state_after=ordering"
    )
    assert (
        json.loads(capsys.readouterr().err)["message"] == "una sencilla"
    )  # the terminal as before


def test_a_restart_adds_to_the_file(tmp_path: Path) -> None:
    log_file = tmp_path / "resto.log"
    configure_logging("INFO", log_file)
    structlog.get_logger().info("telegram_started")
    configure_logging("INFO", log_file)
    structlog.get_logger().info("telegram_started")
    assert len(log_file.read_text(encoding="utf-8").splitlines()) == 2


def test_the_level_applies_to_the_file_too(tmp_path: Path) -> None:
    log_file = tmp_path / "resto.log"
    configure_logging("WARNING", log_file)
    structlog.get_logger().info("turn")
    structlog.get_logger().error("turn_failed", error="ValueError")
    assert "turn_failed" in log_file.read_text(encoding="utf-8")
    assert " turn " not in log_file.read_text(encoding="utf-8")
