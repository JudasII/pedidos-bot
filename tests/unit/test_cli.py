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
