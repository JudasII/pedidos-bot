"""Public-repo guards: secrets and real data must never be committable."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _is_ignored(rel_path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", rel_path],
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "data/private/real_chat.txt",
        "data/private/cases/case_001.yaml",
        "var/pedidos.db",
        "eval/reports/2026-11-04_qwen3.json",
    ],
)
def test_sensitive_paths_are_ignored(path: str) -> None:
    assert _is_ignored(path), f"{path} should be in .gitignore"


@pytest.mark.parametrize("path", [".env.example", "eval/reports/published/summary.md"])
def test_public_paths_are_not_ignored(path: str) -> None:
    assert not _is_ignored(path), f"{path} should not be ignored"


def test_env_example_has_no_secret_values() -> None:
    sensitive = ("TOKEN", "API_KEY", "SECRET", "SALT", "CHAT_ID")
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(s in key for s in sensitive):
            assert value == "", f"{key} must be empty in .env.example"
