"""Customer-facing text, in Colombian Spanish, one version per tone."""

from __future__ import annotations

from enum import StrEnum

from pedidos_bot.config.schema import ToneStyle


class Msg(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    UPDATED = "updated"
    PRODUCT_NOT_FOUND = "product_not_found"
    PRODUCT_CHOICE = "product_choice"
    PRODUCT_YES_NO = "product_yes_no"
    MODIFIER_CHOICE = "modifier_choice"
    MODIFIER_YES_NO = "modifier_yes_no"
    MODIFIER_NOT_FOUND = "modifier_not_found"
    SWITCH_TO_DELIVERY = "switch_to_delivery"
    NOT_IN_CART = "not_in_cart"
    LINE_UNCLEAR = "line_unclear"
    INVALID_CHANGE = "invalid_change"
    FULFILLMENT_UNAVAILABLE = "fulfillment_unavailable"
    PAYMENT_UNKNOWN = "payment_unknown"
    ASK_ITEMS = "ask_items"
    ASK_CHOICE = "ask_choice"
    ANYTHING_ELSE = "anything_else"
    ASK_MORE = "ask_more"
    UPSELL = "upsell"
    ASK_FULFILLMENT = "ask_fulfillment"
    ASK_ADDRESS = "ask_address"
    ASK_PAYMENT = "ask_payment"
    ASK_CASH_CHANGE = "ask_cash_change"
    ASK_CHANGE = "ask_change"
    SUMMARY_TITLE = "summary_title"
    SUMMARY_DELIVERY = "summary_delivery"
    SUMMARY_PICKUP = "summary_pickup"
    CONFIRM_QUESTION = "confirm_question"
    CONFIRMED = "confirmed"
    SENT_TO_STAFF = "sent_to_staff"
    STILL_PENDING = "still_pending"
    ALREADY_CONFIRMED = "already_confirmed"
    CANCELLED = "cancelled"
    HANDOFF = "handoff"
    RESUME_PROMPT = "resume_prompt"
    RECOVER_PROMPT = "recover_prompt"
    NO_LONGER_AVAILABLE = "no_longer_available"
    NOT_UNDERSTOOD = "not_understood"
    QUESTION_UNKNOWN = "question_unknown"
    HOURS = "hours"
    HOURS_UNKNOWN = "hours_unknown"
    PRICE = "price"
    YES = "yes"
    NO = "no"
    OPTION_DELIVERY = "option_delivery"
    OPTION_PICKUP = "option_pickup"


# TODO(#45): some of these sound robotic or are hard to follow; refine them with real chats.
TEMPLATES: dict[ToneStyle, dict[Msg, str]] = {
    ToneStyle.INFORMAL: {
        Msg.ADDED: "Anotado: {item}.",
        Msg.REMOVED: "Listo, quité {item}.",
        Msg.UPDATED: "Listo, quedó así: {item}.",
        Msg.PRODUCT_NOT_FOUND: "No encontré “{text}” en el menú.",
        Msg.PRODUCT_CHOICE: "¿Cuál quieres: {options}?",
        Msg.PRODUCT_YES_NO: "¿Te refieres a {product}?",
        Msg.MODIFIER_CHOICE: "Para {product}, ¿cuál quieres: {options}?",
        Msg.MODIFIER_YES_NO: "Para {product}, ¿te refieres a {option}?",
        Msg.MODIFIER_NOT_FOUND: "No pude ponerle “{text}” a {product}.",
        Msg.SWITCH_TO_DELIVERY: "¿Cambiamos a domicilio a {address}? El envío cuesta {fee}.",
        Msg.NOT_IN_CART: "No tengo “{text}” en tu pedido.",
        Msg.LINE_UNCLEAR: "Tienes varias líneas de {product}. ¿Me dices cuál?",
        Msg.INVALID_CHANGE: "Eso no se puede con {product}: revisa la cantidad o las opciones.",
        Msg.FULFILLMENT_UNAVAILABLE: "Esa opción de entrega no la tenemos.",
        Msg.PAYMENT_UNKNOWN: "No manejamos “{text}”.",
        Msg.ASK_ITEMS: "¿Qué te provoca?",
        Msg.ASK_CHOICE: "¿Qué {group} quieres para {product}?",
        Msg.ANYTHING_ELSE: "¿Algo más?",
        Msg.ASK_MORE: "¿Qué más te anoto?",
        Msg.UPSELL: "¿Le sumamos algo de {options}?",
        Msg.ASK_FULFILLMENT: "¿Lo quieres a domicilio o pasas a recoger?",
        Msg.ASK_ADDRESS: "¿A qué dirección te lo mandamos?",
        Msg.ASK_PAYMENT: "¿Cómo vas a pagar?",
        Msg.ASK_CASH_CHANGE: "¿Con cuánto vas a pagar, para llevarte las vueltas?",
        Msg.ASK_CHANGE: "Dale, ¿qué le cambiamos?",
        Msg.SUMMARY_TITLE: "Este es tu pedido:",
        Msg.SUMMARY_DELIVERY: "Domicilio a {address}",
        Msg.SUMMARY_PICKUP: "Recoges en el local",
        Msg.CONFIRM_QUESTION: "¿Confirmamos?",
        Msg.CONFIRMED: "¡Pedido confirmado! Llega en {eta}.",
        Msg.SENT_TO_STAFF: "¡Listo! Estamos confirmando tu pedido y ya te avisamos.",
        Msg.STILL_PENDING: "Tu pedido sigue en confirmación, ya casi te avisamos.",
        Msg.ALREADY_CONFIRMED: "Tu pedido ya está confirmado.",
        Msg.CANCELLED: "Listo, cancelé tu pedido. Aquí estamos cuando quieras.",
        Msg.HANDOFF: "Ya te paso con alguien del equipo, dame un momento.",
        Msg.RESUME_PROMPT: "¿Seguimos con tu pedido de {items}?",
        Msg.RECOVER_PROMPT: "Tenías un pedido sin terminar: {items}. ¿Lo retomamos?",
        Msg.NO_LONGER_AVAILABLE: "Ya no tenemos {items}.",
        Msg.NOT_UNDERSTOOD: "Perdón, no te entendí. ¿Me lo dices de otra forma?",
        Msg.QUESTION_UNKNOWN: "Eso no te lo sé responder.",
        Msg.HOURS: "Nuestro horario: {hours}.",
        Msg.HOURS_UNKNOWN: "No tengo el horario a la mano.",
        Msg.PRICE: "{product} vale {price}.",
        Msg.YES: "Sí",
        Msg.NO: "No",
        Msg.OPTION_DELIVERY: "Domicilio",
        Msg.OPTION_PICKUP: "Recoger",
    },
    ToneStyle.FORMAL: {
        Msg.ADDED: "Registrado: {item}.",
        Msg.REMOVED: "Con gusto, retiré {item}.",
        Msg.UPDATED: "Con gusto, quedó así: {item}.",
        Msg.PRODUCT_NOT_FOUND: "No encontré “{text}” en el menú.",
        Msg.PRODUCT_CHOICE: "¿Cuál desea: {options}?",
        Msg.PRODUCT_YES_NO: "¿Se refiere a {product}?",
        Msg.MODIFIER_CHOICE: "Para {product}, ¿cuál desea: {options}?",
        Msg.MODIFIER_YES_NO: "Para {product}, ¿se refiere a {option}?",
        Msg.MODIFIER_NOT_FOUND: "No fue posible agregar “{text}” a {product}.",
        Msg.SWITCH_TO_DELIVERY: "¿Desea cambiar a domicilio a {address}? El envío cuesta {fee}.",
        Msg.NOT_IN_CART: "No tengo “{text}” en su pedido.",
        Msg.LINE_UNCLEAR: "Tiene varias líneas de {product}. ¿Podría indicarme cuál?",
        Msg.INVALID_CHANGE: "No es posible con {product}: revise la cantidad o las opciones.",
        Msg.FULFILLMENT_UNAVAILABLE: "No contamos con esa opción de entrega.",
        Msg.PAYMENT_UNKNOWN: "No recibimos “{text}”.",
        Msg.ASK_ITEMS: "¿Qué desea ordenar?",
        Msg.ASK_CHOICE: "¿Qué {group} desea para {product}?",
        Msg.ANYTHING_ELSE: "¿Desea algo más?",
        Msg.ASK_MORE: "¿Qué más desea agregar?",
        Msg.UPSELL: "¿Desea agregar algo de {options}?",
        Msg.ASK_FULFILLMENT: "¿Lo desea a domicilio o pasa a recoger?",
        Msg.ASK_ADDRESS: "¿A qué dirección lo enviamos?",
        Msg.ASK_PAYMENT: "¿Cómo desea pagar?",
        Msg.ASK_CASH_CHANGE: "¿Con qué valor va a pagar, para preparar su cambio?",
        Msg.ASK_CHANGE: "Con gusto, ¿qué desea cambiar?",
        Msg.SUMMARY_TITLE: "Este es su pedido:",
        Msg.SUMMARY_DELIVERY: "Domicilio a {address}",
        Msg.SUMMARY_PICKUP: "Recoge en el local",
        Msg.CONFIRM_QUESTION: "¿Confirma su pedido?",
        Msg.CONFIRMED: "Su pedido está confirmado. Tiempo estimado: {eta}.",
        Msg.SENT_TO_STAFF: "Gracias. Estamos confirmando su pedido y pronto le avisaremos.",
        Msg.STILL_PENDING: "Su pedido sigue en confirmación, pronto le avisaremos.",
        Msg.ALREADY_CONFIRMED: "Su pedido ya está confirmado.",
        Msg.CANCELLED: "Su pedido fue cancelado. Quedamos atentos.",
        Msg.HANDOFF: "Le comunico con alguien del equipo, un momento por favor.",
        Msg.RESUME_PROMPT: "¿Continuamos con su pedido de {items}?",
        Msg.RECOVER_PROMPT: "Tenía un pedido sin terminar: {items}. ¿Desea retomarlo?",
        Msg.NO_LONGER_AVAILABLE: "Ya no contamos con {items}.",
        Msg.NOT_UNDERSTOOD: "Disculpe, no le entendí. ¿Podría decirlo de otra forma?",
        Msg.QUESTION_UNKNOWN: "No tengo esa información.",
        Msg.HOURS: "Nuestro horario: {hours}.",
        Msg.HOURS_UNKNOWN: "No tengo el horario disponible.",
        Msg.PRICE: "{product} tiene un valor de {price}.",
        Msg.YES: "Sí",
        Msg.NO: "No",
        Msg.OPTION_DELIVERY: "Domicilio",
        Msg.OPTION_PICKUP: "Recoger",
    },
}

LIGHT_EMOJI: dict[Msg, str] = {Msg.CONFIRMED: "✅", Msg.SENT_TO_STAFF: "🙌"}
HEAVY_EMOJI: dict[Msg, str] = LIGHT_EMOJI | {
    Msg.ADDED: "📝",
    Msg.CANCELLED: "👋",
    Msg.HANDOFF: "🙋",
    Msg.ANYTHING_ELSE: "😋",
}

WEEKDAYS = {
    "mon": "lunes",
    "tue": "martes",
    "wed": "miércoles",
    "thu": "jueves",
    "fri": "viernes",
    "sat": "sábado",
    "sun": "domingo",
}
