"""Structured logs: one JSON object per line in the terminal, and optionally a plain-text file.

The file is for reading later what happened in a session. It holds what customers wrote
(masked), so keep it under `data/private/`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, TextIO

import structlog

_open_file: TextIO | None = None


class _AlsoToFile:
    """The last processor: writes a readable line to the file, returns JSON for the terminal."""

    def __init__(self, file: TextIO) -> None:
        self._file = file
        self._plain = structlog.dev.ConsoleRenderer(colors=False)
        self._json = structlog.processors.JSONRenderer()

    def __call__(self, logger: Any, name: str, event: dict[str, Any]) -> str:
        self._file.write(f"{self._plain(logger, name, dict(event))}\n")
        return str(self._json(logger, name, event))


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    global _open_file
    if _open_file is not None:
        _open_file.close()
        _open_file = None
    render: Any = structlog.processors.JSONRenderer()
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # Line-buffered: each line is on disk at once, so `tail -f` works and Ctrl-C loses none.
        _open_file = log_file.open("a", encoding="utf-8", buffering=1)
        render = _AlsoToFile(_open_file)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            render,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
    )
