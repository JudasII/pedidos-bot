from pathlib import Path

import pytest

from pedidos_bot import __version__
from pedidos_bot.cli import main


def test_main_without_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "pedidos-bot" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_config_schema_doc_is_up_to_date() -> None:

    from pedidos_bot.config.loader import config_json_schema_text

    doc = Path(__file__).resolve().parents[2] / "docs" / "config.schema.json"
    assert doc.read_text(encoding="utf-8") == config_json_schema_text(), (
        "run: uv run pedidos-bot config schema --output docs/config.schema.json"
    )


def test_config_validate_reports_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("restaurant: {}\n", encoding="utf-8")
    assert main(["config", "validate", str(bad)]) == 1
    assert "configuration error" in capsys.readouterr().err


def test_resolve_command(capsys: pytest.CaptureFixture[str]) -> None:
    root = Path(__file__).resolve().parents[2]
    args = [
        "resolve",
        "--restaurant",
        "burger_demo",
        "--config-dir",
        str(root / "config/restaurants"),
    ]
    assert main([*args, "malteda de fresa"]) == 0
    out = capsys.readouterr().out
    assert "product: resolved" in out and "sabor_fresa" in out
