"""The exact text the restaurant and the customer read, line by line.

Mutation testing showed that checking for a phrase let extra characters and lost lines pass.
"""

from pedidos_bot.channels.base import GeoPoint, MessageKind
from pedidos_bot.engine.actions import AddItem
from pedidos_bot.llm.fake import ScriptedLLM
from tests.e2e.harness import Chat
from tests.factories import sample_config

BURGER = sample_config("burger_demo")
MINIMAL = sample_config("minimal_demo")


async def test_the_order_notification_reads_line_by_line() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="tenders", note="bien crocantes")]]))
    for text in ("unos tenders", "no", "Ana", "3001234567", "para recoger", "nequi", "sí"):
        await chat.say(text)
    code = chat.conversation.code
    notice = chat.staff[-1]
    assert notice.text.splitlines() == [
        f"🧾 Pedido {code}",
        "Cliente: Ana · 300 123 4567",
        "• 1 x Chicken Tenders: $16.000",
        "   Nota: bien crocantes",
        "Recoge en el local",
        "Total: $16.000",
        "Pago: Transferencia (Nequi / Bancolombia)",
    ]
    assert [r.title for r in notice.quick_replies] == ["✓ Aprobar", "✗ Rechazar"]


async def test_an_order_already_confirmed_says_so_on_its_own_line() -> None:
    chat = Chat(MINIMAL)
    for text in ("un perrito", "no", "Ana", "3001234567", "efectivo", "exacto", "sí"):
        await chat.say(text)
    lines = chat.staff[-1].text.splitlines()
    assert lines[-2:] == ["Pago: Efectivo", "Confirmado con el cliente."]


async def test_a_switch_back_to_pickup_is_shown_as_pickup() -> None:
    chat = Chat(BURGER)
    for text in ("unos tenders", "no", "Ana", "3001234567", "domicilio", "calle 10 # 5-20"):
        await chat.say(text)
    await chat.say("para recoger")  # the address stays in the cart
    summary = await chat.say("nequi")
    assert "Recoges en el local" in summary and "Domicilio" not in summary
    await chat.say("sí")
    assert "Recoge en el local" in chat.staff[-1].text.splitlines()


async def test_a_rejection_notice_says_rejected() -> None:
    chat = Chat(BURGER)
    for text in ("unos tenders", "no", "Ana", "3001234567", "para recoger", "nequi", "sí"):
        await chat.say(text)
    await chat.tap("Rechazar")
    assert (
        chat.staff[-1].text
        == f"Pedido {chat.conversation.code} rechazado ❌. Ya le avisé al cliente."
    )


async def test_the_handoff_notice_reads_line_by_line() -> None:
    chat = Chat(BURGER)
    await chat.say("unos tenders")
    await chat.say("una corona")
    await chat.say("asesor")
    code = chat.conversation.code
    assert chat.staff[-1].text.splitlines() == [
        f"🙋 Cliente #{code} pide hablar con una persona.",
        "Escribió: “asesor”",
        "Pedido en curso: 1 x Chicken Tenders, 1 x Corona",
        f"Responde a este mensaje para escribirle (o /responder {code} tu mensaje). "
        f"Para devolverlo al asistente: /devolver {code}.",
    ]


async def test_a_photo_with_a_caption_reaches_the_staff_with_it() -> None:
    chat = Chat(BURGER)
    await chat.say("asesor")
    code = chat.conversation.code
    photo = chat.message("así la quiero", MessageKind.IMAGE)
    await chat.send(photo)
    assert chat.staff[-1].text == f"💬 #{code}: [foto] así la quiero"
    point = chat.message(None, MessageKind.LOCATION).model_copy(
        update={"location": GeoPoint(latitude=4.6, longitude=-74.1)}
    )
    await chat.send(point)
    assert chat.staff[-1].text == f"💬 #{code}: 📍 https://maps.google.com/?q=4.6,-74.1"


async def test_the_menu_and_hours_are_laid_out() -> None:
    chat = Chat(BURGER)
    await chat.say("una pizza")
    menu = await chat.say("sí")
    assert "• Hamburguesa Especial: $28.000\n\nAcompañantes\n• Chicken Tenders" in menu
    hours = await chat.say("¿a qué hora abren?")
    assert "domingo de 12:00 a 22:00; viernes, sábado de 12:00 a 23:30." in hours
