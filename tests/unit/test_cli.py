import asyncio
from pathlib import Path

import pytest

from pedidos_bot import __version__
from pedidos_bot.app import run_chat
from pedidos_bot.cli import main
from pedidos_bot.config.loader import load_restaurant

ROOT = Path(__file__).resolve().parents[2]


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


def test_chat_runs_a_conversation_until_the_exit_command() -> None:
    config = load_restaurant("minimal_demo", ROOT / "config" / "restaurants")
    said = iter(["un suizo", "/salir"])
    output: list[str] = []
    asyncio.run(run_chat(config, read=lambda _: next(said), write=output.append))
    text = "\n".join(output)
    assert "Registrado: 1 x Perro Suizo" in text
    assert "[ordering]" in text


def test_chat_ends_on_end_of_input() -> None:
    config = load_restaurant("minimal_demo", ROOT / "config" / "restaurants")

    def closed(_: str) -> str:
        raise EOFError

    asyncio.run(run_chat(config, read=closed, write=lambda _: None))


def test_chat_reports_an_unknown_restaurant(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["chat", "--restaurant", "nope"]) == 1
    assert "file not found" in capsys.readouterr().err


def test_run_names_the_missing_settings_without_their_values(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:hunter2@localhost/db")
    config_dir = str(ROOT / "config" / "restaurants")
    assert main(["run", "--restaurant", "minimal_demo", "--config-dir", config_dir]) == 1
    err = capsys.readouterr().err
    assert "TELEGRAM_BOT_TOKEN" in err
    assert "hunter2" not in err
