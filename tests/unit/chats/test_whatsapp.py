"""Reading WhatsApp exports. Every chat here is invented."""

from datetime import datetime

import pytest

from pedidos_bot.chats.whatsapp import parse

ANDROID = """\
12/11/26, 8:44 p. m. - Los mensajes y las llamadas están cifrados de extremo a extremo.
12/11/26, 8:45 p. m. - Ana Demo: Hola buenas
12/11/26, 8:45 p. m. - Ana Demo: me regalas una hamburguesa sencilla
sin cebolla porfa
12/11/26, 8:46 p. m. - Local Demo: Claro que sí, ¿para domicilio?
12/11/26, 8:47 p. m. - Ana Demo: <Multimedia omitido>
12/11/26, 8:48 p. m. - Ana Demo: PTT-20261112-WA0003.opus (archivo adjunto)
12/11/26, 8:49 p. m. - Ana Demo: Ubicación: https://maps.google.com/?q=4.6,-74.1
12/11/26, 8:50 p. m. - Ana Demo: Se eliminó este mensaje
"""

IPHONE = """\
\u200e[12/11/26, 8:45:10\u202fp.\u00a0m.] Ana Demo: Hola
[12/11/26, 8:45:40\u202fp.\u00a0m.] Local Demo: \u200e<adjunto: 00000007-PHOTO-2026-11-12.jpg>
[12/11/26, 8:46:02\u202fp.\u00a0m.] Ana Demo: \u200esticker omitido
[13/11/26, 12:05:00\u202fa.\u00a0m.] Ana Demo: gracias
"""


def test_an_android_export_is_read() -> None:
    chat = parse(ANDROID)
    assert chat.notices == 1 and chat.stray_lines == 0
    assert chat.senders == {"Ana Demo", "Local Demo"}
    first = chat.messages[0]
    assert (first.sender, first.text, first.kind) == ("Ana Demo", "Hola buenas", "text")
    assert first.at == datetime(2026, 11, 12, 20, 45)


def test_a_message_can_span_lines() -> None:
    assert (
        parse(ANDROID).messages[1].text == "me regalas una hamburguesa sencilla\nsin cebolla porfa"
    )


def test_what_is_not_text_keeps_its_kind_and_nothing_else() -> None:
    kinds = [(message.kind, message.text) for message in parse(ANDROID).messages[3:]]
    assert kinds == [("media", ""), ("audio", ""), ("location", ""), ("deleted", "")]


def test_an_iphone_export_is_read() -> None:
    chat = parse(IPHONE)
    assert [(m.sender, m.kind, m.text) for m in chat.messages] == [
        ("Ana Demo", "text", "Hola"),
        ("Local Demo", "image", ""),
        ("Ana Demo", "sticker", ""),
        ("Ana Demo", "text", "gracias"),
    ]
    assert chat.messages[0].at == datetime(2026, 11, 12, 20, 45)
    assert chat.messages[-1].at == datetime(2026, 11, 13, 0, 5)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("12/11/2026, 20:45 - Ana Demo: hola", datetime(2026, 11, 12, 20, 45)),
        ("12/11/26, 12:30 p. m. - Ana Demo: hola", datetime(2026, 11, 12, 12, 30)),
        ("1/2/26, 9:05 AM - Ana Demo: hola", datetime(2026, 2, 1, 9, 5)),
    ],
)
def test_dates_are_day_first_with_either_clock(line: str, expected: datetime) -> None:
    assert parse(line).messages[0].at == expected


def test_a_phone_set_to_month_first_can_be_read_too() -> None:
    chat = parse("11/12/26, 8:45 PM - Ana Demo: hola", month_first=True)
    assert chat.messages[0].at == datetime(2026, 11, 12, 20, 45)


def test_a_colon_in_the_text_stays_in_the_text() -> None:
    chat = parse("12/11/26, 8:45 p. m. - Ana Demo: dirección: calle 1 # 2-3")
    assert chat.messages[0].text == "dirección: calle 1 # 2-3"


def test_a_file_in_an_unknown_format_is_reported_not_guessed() -> None:
    chat = parse("Ana Demo (8:45 PM): hola\nLocal Demo (8:46 PM): buenas\n")
    assert chat.messages == [] and chat.stray_lines == 2


def test_text_after_a_notice_is_not_glued_to_the_message_before() -> None:
    export = (
        "12/11/26, 8:45 p. m. - Ana Demo: hola\n"
        "12/11/26, 8:46 p. m. - Ana Demo cambió su número de teléfono.\n"
        "Toca para enviar un mensaje.\n"
    )
    chat = parse(export)
    assert [m.text for m in chat.messages] == ["hola"]
    assert chat.notices == 1


def test_a_link_inside_a_sentence_is_still_text() -> None:
    chat = parse("12/11/26, 8:45 p. m. - Ana Demo: es aquí https://maps.google.com/?q=4.6,-74.1")
    assert chat.messages[0].kind == "text"
    assert parse("12/11/26, 8:45 p. m. - Ana Demo: https://maps.app.goo.gl/abc").messages[
        0
    ].kind == ("location")
