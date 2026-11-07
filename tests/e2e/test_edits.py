"""Changing an order in progress. Added after mutation testing found these paths unguarded."""

from pedidos_bot.channels.base import MessageKind
from pedidos_bot.engine.actions import AddItem, Deny, RemoveItem, Unknown, UpdateItem
from pedidos_bot.engine.lifecycle import LifecycleState as S
from pedidos_bot.llm.fake import ScriptedLLM
from tests.e2e.harness import Chat
from tests.factories import config_dict, make_config, sample_config

BURGER = sample_config("burger_demo")
SENCILLA = "hamburguesa_sencilla"


async def test_removing_part_of_a_line_keeps_the_rest() -> None:
    chat = Chat(BURGER)
    await chat.say("3 sencillas")
    assert "quité 3 x" in await chat.say("quita 2 sencillas")
    assert chat.lines == [(SENCILLA, 1, set())]


async def test_removing_one_with_the_fake_llm_only_removes_one() -> None:
    chat = Chat(BURGER)
    await chat.say("3 sencillas")
    await chat.say("quita 1 sencilla")
    assert chat.lines == [(SENCILLA, 2, set())]


async def test_removing_as_many_as_there_are_removes_the_line() -> None:
    llm = ScriptedLLM(
        [[AddItem(product="sencilla", quantity=2)], [RemoveItem(product="sencilla", quantity=5)]]
    )
    chat = Chat(BURGER, llm)
    await chat.say("2 sencillas")
    await chat.say("quita 5")
    assert chat.lines == []


async def test_removing_something_not_in_the_cart_says_so() -> None:
    chat = Chat(BURGER)
    await chat.say("una sencilla")
    assert "No tengo “coca” en tu pedido" in await chat.say("quita la coca")
    assert chat.lines == [(SENCILLA, 1, set())]


async def test_changing_the_quantity() -> None:
    chat = Chat(BURGER)
    await chat.say("una sencilla")
    assert "quedó así: 3 x" in await chat.say("mejor que sean 3 sencillas")
    assert chat.lines == [(SENCILLA, 3, set())]


async def test_adding_and_removing_modifiers_on_a_line() -> None:
    llm = ScriptedLLM(
        [
            [AddItem(product="sencilla", modifiers=("sin tomate",))],
            [
                UpdateItem(
                    product="sencilla",
                    add_modifiers=("tocineta",),
                    remove_modifiers=("sin tomate",),
                )
            ],
        ]
    )
    chat = Chat(BURGER, llm)
    await chat.say("una sencilla sin tomate")
    await chat.say("mejor con tocineta y con tomate")
    assert chat.lines == [(SENCILLA, 1, {"extra_tocineta"})]


async def test_a_modifier_that_does_not_exist_changes_nothing() -> None:
    llm = ScriptedLLM(
        [
            [AddItem(product="sencilla")],
            [UpdateItem(product="sencilla", add_modifiers=("piña",))],
        ]
    )
    chat = Chat(BURGER, llm)
    for text in ("una sencilla", "no", "para recoger", "nequi", "sí"):
        await chat.say(text)
    reply = await chat.say("ponle piña")
    assert "No pude ponerle “piña”" in reply and "quedó así" not in reply
    assert chat.state is S.PENDING_STAFF_CONFIRMATION


async def test_a_line_that_is_not_clear_is_asked_about() -> None:
    llm = ScriptedLLM(
        [
            [AddItem(product="sencilla"), AddItem(product="sencilla", modifiers=("sin tomate",))],
            [UpdateItem(product="sencilla", quantity=3)],
        ]
    )
    chat = Chat(BURGER, llm)
    await chat.say("una sencilla y otra sin tomate")
    assert "varias líneas" in await chat.say("que sean 3")
    assert [line[1] for line in chat.lines] == [1, 1]


async def test_an_edit_that_merges_lines_says_how_it_ended_up() -> None:
    llm = ScriptedLLM(
        [
            [AddItem(product="sencilla"), AddItem(product="sencilla", modifiers=("sin tomate",))],
            [UpdateItem(product="sencilla sin tomate", remove_modifiers=("sin tomate",))],
        ]
    )
    chat = Chat(BURGER, llm)
    await chat.say("una sencilla y otra sin tomate")
    assert "quedó así: 2 x" in await chat.say("con tomate")
    assert chat.lines == [(SENCILLA, 2, set())]


async def test_no_with_an_item_means_the_customer_is_not_done() -> None:
    llm = ScriptedLLM(
        [[AddItem(product="sencilla")], [Deny(), AddItem(product="agua")]],
    )
    chat = Chat(BURGER, llm)
    await chat.say("una sencilla")
    await chat.say("no, mejor agüita")
    assert not chat.conversation.items_done


async def test_an_answer_to_an_unclear_modifier_with_more_in_it_keeps_the_rest() -> None:
    chat = Chat(BURGER)
    await chat.say("una sencilla con cebolla")
    await chat.say("sin cebolla y unas papas")
    assert "papas_fritas" in [line[0] for line in chat.lines]
    assert chat.conversation.clarifications != ()


async def test_an_option_from_another_group_does_not_answer_a_required_choice() -> None:
    data = config_dict()
    soda = data["categories"][1]["products"][0]
    soda["modifier_groups"] = [*soda["modifier_groups"], "extras"]
    llm = ScriptedLLM([[AddItem(product=soda["aliases"][0])], [Unknown()]])
    chat = Chat(make_config(**data), llm)
    await chat.say("una gaseosa")
    reply = await chat.say("huevo")
    assert "tamaño" in reply and "no se puede" not in reply.lower()
    assert chat.lines[0][2] == set()


async def test_choosing_the_mode_first_still_asks_for_anything_else() -> None:
    chat = Chat(BURGER)
    await chat.say("a domicilio")
    assert "Le sumamos" in await chat.say("una sencilla")


async def test_a_clear_action_at_the_address_question_is_not_the_address() -> None:
    llm = ScriptedLLM([[AddItem(product="sencilla")], [AddItem(product="agua")]])
    chat = Chat(BURGER, llm)
    for text in ("una sencilla", "no", "domicilio"):
        await chat.say(text)
    await chat.say("ah, y un agua")
    assert chat.conversation.cart.address is None
    assert "agua" in [line[0] for line in chat.lines]


async def test_a_menu_change_before_anything_is_in_the_cart_needs_no_recovery() -> None:
    data = config_dict()
    chat = Chat(make_config(**data))
    await chat.say("hola")
    data["categories"][0]["products"][0]["price"] += 1000
    changed = Chat(make_config(**data), repository=chat.repository, first_message_id=100)
    await changed.say("una clasica")
    assert "¿Lo retomamos?" not in await changed.say("otra clasica")


async def test_a_voice_note_after_a_while_still_goes_to_a_person() -> None:
    chat = Chat(BURGER)
    await chat.say("una sencilla")
    chat.wait(hours=3)
    await chat.send(chat.message(None, MessageKind.VOICE))
    assert chat.state is S.HANDOFF
