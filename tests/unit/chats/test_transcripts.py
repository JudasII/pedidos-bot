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


def test_two_snapshots_of_one_chat_cannot_tell_who_the_restaurant_is(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "lunes.txt").write_text(ANA, encoding="utf-8")
    (inbox / "martes.txt").write_text(ANA + EXTRA, encoding="utf-8")
    with pytest.raises(ChatImportError, match="--restaurant") as raised:
        import_exports([inbox], tmp_path / "out", salt=SALT)
    assert "Ana Prueba" in str(raised.value) and "Local Demo" in str(raised.value)
    assert not (tmp_path / "out").exists()


EXTRA = "12/11/26, 8:55 p. m. - Ana Prueba: gracias!\n"
SHORT = "".join(ANA.splitlines(keepends=True)[:2])


def _import(tmp_path: Path, name: str, export: str) -> tuple[int, int]:
    """Import one export; returns (lines in the transcript, lines that were new)."""
    path = tmp_path / name
    path.write_text(export, encoding="utf-8")
    [result] = import_exports([path], tmp_path / "out", salt=SALT, restaurant=["Local Demo"])
    return result.messages, result.added


def _stored(tmp_path: Path) -> list[str]:
    [path] = (tmp_path / "out" / "masked").glob("*.json")
    return [line["text"] for line in json.loads(path.read_text(encoding="utf-8"))["lines"]]


def test_a_shorter_snapshot_after_a_longer_one_loses_nothing(tmp_path: Path) -> None:
    assert _import(tmp_path, "full.txt", ANA) == (6, 6)
    before = _stored(tmp_path)
    assert _import(tmp_path, "short.txt", SHORT) == (6, 0)
    assert _stored(tmp_path) == before


def test_a_longer_snapshot_adds_what_is_new(tmp_path: Path) -> None:
    assert _import(tmp_path, "short.txt", SHORT) == (2, 2)
    assert _import(tmp_path, "full.txt", ANA + EXTRA) == (7, 5)
    assert _stored(tmp_path)[-1] == "gracias!"


def test_snapshots_that_overlap_in_the_middle_are_joined_in_order(tmp_path: Path) -> None:
    lines = (ANA + EXTRA).splitlines(keepends=True)
    assert _import(tmp_path, "early.txt", "".join(lines[:4])) == (4, 4)
    assert _import(tmp_path, "late.txt", "".join(lines[2:])) == (7, 3)
    assert _stored(tmp_path) == [
        line.text for line in mask(parse(ANA + EXTRA), {"Local Demo"}, SALT).lines
    ]


def test_messages_that_repeat_are_not_collapsed(tmp_path: Path) -> None:
    twice = "12/11/26, 9:00 p. m. - Ana Prueba: ok\n12/11/26, 9:00 p. m. - Ana Prueba: ok\n"
    assert _import(tmp_path, "a.txt", ANA + twice) == (8, 8)
    assert _import(tmp_path, "b.txt", ANA + twice) == (8, 0)


def test_a_message_that_differs_between_snapshots_is_kept_both_ways(tmp_path: Path) -> None:
    deleted = ANA.replace("Hola! ¿A dónde te lo enviamos?", "Se eliminó este mensaje")
    _import(tmp_path, "before.txt", ANA)
    assert _import(tmp_path, "after.txt", deleted) == (7, 1)


# --- what mutation testing showed unguarded -----------------------------------------------------


def test_a_name_is_masked_whatever_its_case() -> None:
    chat = parse("12/11/26, 8:45 p. m. - Ana Prueba: hola, soy ana prueba\n")
    [line] = mask(chat, {"Local Demo"}, SALT).lines
    assert line.text == "hola, soy [name] [name]"


def test_a_sender_known_only_by_number_leaves_the_text_alone() -> None:
    lines = mask(parse(LUIS), {"Local Demo"}, SALT).lines
    assert [line.text for line in lines] == ["buenas, una sencilla", "con gusto"]


def test_files_and_folders_can_be_imported_together(tmp_path: Path) -> None:
    inbox = _exports(tmp_path / "inbox")
    single = tmp_path / "single.txt"
    single.write_text(ANA.replace("Ana Prueba", "Eva Prueba"), encoding="utf-8")
    results = import_exports([single, inbox], tmp_path / "out", salt=SALT)
    assert len(results) == 3


def test_bytes_that_are_not_utf8_are_replaced_not_fatal(tmp_path: Path) -> None:
    export = tmp_path / "chat.txt"
    export.write_bytes(ANA.encode() + b"12/11/26, 9:00 p. m. - Ana Prueba: caf\xe9\n")
    [result] = import_exports([export], tmp_path / "out", salt=SALT, restaurant=["Local Demo"])
    assert result.messages == 7
    with zipfile.ZipFile(tmp_path / "chat.zip", "w") as archive:
        archive.writestr("_chat.txt", ANA.encode() + b"12/11/26, 9:01 p. m. - Ana Prueba: \xff\n")
    [zipped] = import_exports(
        [tmp_path / "chat.zip"], tmp_path / "out2", salt=SALT, restaurant=["Local Demo"]
    )
    assert zipped.messages == 7


def test_broken_archives_say_what_is_wrong(tmp_path: Path) -> None:
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("foto.jpg", b"x")
    with pytest.raises(ChatImportError, match="no chat text inside"):
        import_exports([empty], tmp_path / "out", salt=SALT, restaurant=["Local Demo"])
    fake = tmp_path / "fake.zip"
    fake.write_text("not a zip", encoding="utf-8")
    with pytest.raises(ChatImportError, match="not a zip file"):
        import_exports([fake], tmp_path / "out", salt=SALT, restaurant=["Local Demo"])


def test_month_first_dates_reach_the_transcript(tmp_path: Path) -> None:
    export = tmp_path / "chat.txt"
    export.write_text("11/13/26, 8:45 PM - Ana Prueba: hola\n", encoding="utf-8")
    import_exports(
        [export], tmp_path / "out", salt=SALT, restaurant=["Local Demo"], month_first=True
    )
    assert _stored(tmp_path) == ["hola"]
    [path] = (tmp_path / "out" / "masked").glob("*.json")
    assert '"2026-11-13T20:45:00"' in path.read_text(encoding="utf-8")


def test_an_unreadable_export_does_not_stop_the_rest(tmp_path: Path) -> None:
    inbox = _exports(tmp_path / "inbox")
    (inbox / "0-raro.txt").write_text("Ana (8:45): hola\n", encoding="utf-8")  # sorted first
    results = import_exports([inbox], tmp_path / "out", salt=SALT)
    assert sum(1 for result in results if result.chat_id is not None) == 2


def test_chats_where_only_the_restaurant_writes_stay_apart(tmp_path: Path) -> None:
    for name in ("uno.txt", "dos.txt"):
        (tmp_path / name).write_text(
            f"12/11/26, 8:45 p. m. - Local Demo: {name}\n", encoding="utf-8"
        )
    results = import_exports(
        [tmp_path / "uno.txt", tmp_path / "dos.txt"], tmp_path / "out", salt=SALT,
        restaurant=["Local Demo"],
    )  # fmt: skip
    assert len({result.chat_id for result in results}) == 2


def test_long_snapshots_with_repeated_lines_are_joined_without_growing(tmp_path: Path) -> None:
    # Times only go forward in an export: ten messages a minute for half an hour.
    said = ["ok" if n % 3 else str(n) for n in range(300)]
    rows = [f"12/11/26, 8:{n // 10:02d} p. m. - Ana Prueba: {text}" for n, text in enumerate(said)]
    export = "\n".join(rows) + "\n"
    first = _import(tmp_path, "a.txt", export)
    assert _import(tmp_path, "b.txt", export) == (first[0], 0)


def test_one_export_with_one_sender_needs_the_restaurant_named(tmp_path: Path) -> None:
    export = tmp_path / "solo.txt"
    export.write_text("12/11/26, 8:45 p. m. - Ana Prueba: hola\n", encoding="utf-8")
    with pytest.raises(ChatImportError, match="one chat alone can't tell"):
        import_exports([export], tmp_path / "out", salt=SALT)


def test_why_the_restaurant_is_unclear_is_spelled_out(tmp_path: Path) -> None:
    for name, sender in (("a.txt", "Ana Prueba"), ("b.txt", "Beto Prueba")):
        (tmp_path / name).write_text(f"12/11/26, 8:45 p. m. - {sender}: hola\n", encoding="utf-8")
    with pytest.raises(ChatImportError) as raised:
        import_exports([tmp_path / "a.txt", tmp_path / "b.txt"], tmp_path / "out", salt=SALT)
    assert str(raised.value) == (
        "can't tell who the restaurant is (nobody writes in every chat): pass its name as it "
        "appears in the chats with --restaurant"
    )


def test_two_exports_with_the_same_name_are_both_kept(tmp_path: Path) -> None:
    for folder, text in (("lunes", ANA), ("martes", ANA + EXTRA)):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "chat.txt").write_text(text, encoding="utf-8")
    import_exports([tmp_path / "lunes", tmp_path / "martes"], tmp_path / "out", salt=SALT,
                   restaurant=["Local Demo"])  # fmt: skip
    assert len(list((tmp_path / "out" / "raw").iterdir())) == 2


def test_the_reasons_are_spelled_out_exactly(tmp_path: Path) -> None:
    export = tmp_path / "solo.txt"
    export.write_text("12/11/26, 8:45 p. m. - Ana Prueba: hola\n", encoding="utf-8")
    with pytest.raises(ChatImportError) as alone:
        import_exports([export], tmp_path / "out", salt=SALT)
    assert "(one chat alone can't tell)" in str(alone.value)
    (tmp_path / "otra.txt").write_text(ANA + EXTRA, encoding="utf-8")
    (tmp_path / "una.txt").write_text(ANA, encoding="utf-8")
    with pytest.raises(ChatImportError) as shared:
        import_exports([tmp_path / "una.txt", tmp_path / "otra.txt"], tmp_path / "out", salt=SALT)
    assert "(Ana Prueba, Local Demo all write in every chat)" in str(shared.value)
