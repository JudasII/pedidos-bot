"""Masking and importing chat exports. Every chat here is invented."""

import json
import zipfile
from pathlib import Path

import pytest

from pedidos_bot.chats.transcripts import ChatImportError, import_exports, mask
from pedidos_bot.chats.whatsapp import parse
from pedidos_bot.cli import main

SALT = "test-salt"
ANA = """\
12/11/26, 8:45 p. m. - Ana Prueba: Hola, soy Ana, mi cel es 300 123 4567
12/11/26, 8:46 p. m. - Local Demo: Hola! ¿A dónde te lo enviamos?
12/11/26, 8:47 p. m. - Ana Prueba: Cra 7 # 45-10 apto 301, escríbeme a ana.prueba@example.com
12/11/26, 8:48 p. m. - Ana Prueba: mira https://maps.google.com/?q=4.6,-74.1 ahí es
12/11/26, 8:49 p. m. - Ana Prueba: PTT-20261112-WA0003.opus (archivo adjunto)
12/11/26, 8:50 p. m. - Local Demo: Listo Ana, una Demo doble en camino
"""
LUIS = """\
13/11/26, 1:05 p. m. - +57 311 555 0000: buenas, una sencilla
13/11/26, 1:06 p. m. - Local Demo: con gusto
"""


def test_masking_removes_who_and_where_but_keeps_what_was_said() -> None:
    transcript = mask(parse(ANA), {"Local Demo"}, SALT)
    assert [line.role for line in transcript.lines] == [
        "customer", "restaurant", "customer", "customer", "customer", "restaurant",
    ]  # fmt: skip
    texts = [line.text for line in transcript.lines]
    assert texts[0] == "Hola, soy [name], mi cel es [phone]"
    assert texts[2] == "[address], escríbeme a [email]"
    assert texts[3] == "mira [link] ahí es"
    assert (transcript.lines[4].kind, texts[4]) == ("audio", "")
    dumped = transcript.model_dump_json()
    for secret in ("Ana", "300 123", "45-10", "example.com", "maps.google", "WA0003"):
        assert secret not in dumped


def test_the_restaurant_name_stays_because_it_can_be_a_menu_word() -> None:
    transcript = mask(parse(ANA), {"Local Demo"}, SALT)
    assert transcript.lines[-1].text == "Listo [name], una Demo doble en camino"


def test_the_chat_id_is_stable_and_says_nothing() -> None:
    first = mask(parse(ANA), {"Local Demo"}, SALT)
    again = mask(parse(ANA), {"Local Demo"}, SALT)
    other_salt = mask(parse(ANA), {"Local Demo"}, "another")
    assert first.chat_id == again.chat_id != other_salt.chat_id
    assert "ana" not in first.chat_id.lower()


def test_several_people_in_a_group_get_numbered_roles() -> None:
    group = ANA + "12/11/26, 8:51 p. m. - Beto Prueba: y a mí una malteada\n"
    roles = {line.role for line in mask(parse(group), {"Local Demo"}, SALT).lines}
    assert roles == {"restaurant", "customer", "customer_2"}


def _exports(folder: Path) -> Path:
    folder.mkdir()
    (folder / "Chat de WhatsApp con Ana Prueba.txt").write_text(ANA, encoding="utf-8")
    with zipfile.ZipFile(folder / "Chat de WhatsApp con Luis.zip", "w") as archive:
        archive.writestr("_chat.txt", LUIS)
        archive.writestr("PTT-20261113-WA0001.opus", b"not audio")
    return folder


def test_importing_stores_the_exports_and_writes_masked_transcripts(tmp_path: Path) -> None:
    out = tmp_path / "data" / "private" / "chats"
    results = import_exports([_exports(tmp_path / "inbox")], out, salt=SALT)
    assert [result.messages for result in results] == [6, 2]
    assert len(list((out / "raw").iterdir())) == 2
    masked = sorted((out / "masked").glob("*.json"))
    assert {path.stem for path in masked} == {result.chat_id for result in results}
    everything = "".join(path.read_text(encoding="utf-8") for path in masked)
    assert "311 555" not in everything and "Ana" not in everything
    roles = {line["role"] for path in masked for line in json.loads(path.read_text())["lines"]}
    assert roles == {"restaurant", "customer"}  # found without being told: it is in every chat


def test_importing_twice_changes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    inbox = _exports(tmp_path / "inbox")
    import_exports([inbox], out, salt=SALT)
    import_exports([inbox], out, salt=SALT)
    assert len(list((out / "raw").iterdir())) == 2
    assert len(list((out / "masked").iterdir())) == 2


def test_one_chat_alone_needs_the_restaurant_named(tmp_path: Path) -> None:
    export = tmp_path / "chat.txt"
    export.write_text(ANA, encoding="utf-8")
    with pytest.raises(ChatImportError, match="--restaurant"):
        import_exports([export], tmp_path / "out", salt=SALT)
    [result] = import_exports([export], tmp_path / "out", salt=SALT, restaurant=["Local Demo"])
    assert result.messages == 6 and dict(result.kinds) == {"audio": 1, "text": 5}


def test_an_export_that_cannot_be_read_is_kept_and_reported(tmp_path: Path) -> None:
    inbox = _exports(tmp_path / "inbox")
    (inbox / "raro.txt").write_text("Ana (8:45): hola\nLocal (8:46): buenas\n", encoding="utf-8")
    results = import_exports([inbox], tmp_path / "out", salt=SALT)
    unread = [result for result in results if result.chat_id is None]
    assert [(result.export.name, result.stray_lines) for result in unread] == [("raro.txt", 2)]
    assert len(list((tmp_path / "out" / "raw").iterdir())) == 3


def test_a_missing_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ChatImportError, match="not found"):
        import_exports([tmp_path / "nope.txt"], tmp_path / "out", salt=SALT)


def test_the_command_only_writes_inside_data_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PII_HASH_SALT", SALT)
    inbox = str(_exports(tmp_path / "inbox"))
    assert main(["chats", "import", inbox, "--out", str(tmp_path / "elsewhere")]) == 1
    assert "data/private" in capsys.readouterr().err
    assert not (tmp_path / "elsewhere").exists()
    assert main(["chats", "import", inbox]) == 0
    out = capsys.readouterr().out
    assert "2 transcripts" in out and "best effort" in out
    assert len(list((tmp_path / "data" / "private" / "chats" / "masked").iterdir())) == 2


def test_the_command_needs_the_salt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PII_HASH_SALT", "")
    assert main(["chats", "import", str(_exports(tmp_path / "inbox"))]) == 1
    assert "PII_HASH_SALT" in capsys.readouterr().err
