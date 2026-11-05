"""Guardas del repo público: secretos y datos reales nunca deben quedar versionables."""

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
        "data/private/chat_real.txt",
        "data/private/cases/caso_001.yaml",
        "var/pedidos.db",
        "eval/reports/2026-11-04_qwen3.json",
    ],
)
def test_rutas_sensibles_estan_ignoradas(path: str) -> None:
    assert _is_ignored(path), f"{path} debería estar en .gitignore"


@pytest.mark.parametrize("path", [".env.example", "eval/reports/published/resumen.md"])
def test_rutas_publicas_no_estan_ignoradas(path: str) -> None:
    assert not _is_ignored(path), f"{path} no debería estar ignorado"


def test_env_example_no_tiene_valores_de_secretos() -> None:
    sensibles = ("TOKEN", "API_KEY", "SALT", "CHAT_ID")
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(s in key for s in sensibles):
            assert value == "", f"{key} no debe traer valor en .env.example"
