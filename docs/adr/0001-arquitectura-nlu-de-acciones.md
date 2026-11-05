# ADR-0001: LLM como NLU de acciones + carrito como estado + política determinista

- Estado: aceptada
- Fecha: 2026-11-04

## Contexto
El bot debe tomar pedidos en español colombiano informal con un modelo local de 4B parámetros (Qwen3:4b vía Ollama) y, después, posiblemente con un modelo hospedado. Los clientes mandan varios datos en un mensaje ("2 sencillas, una sin cebolla, una coca grande, a la calle 10, pago en efectivo"), corrigen a mitad de pedido y cancelan. El LLM nunca puede inventar productos ni precios.

La propuesta inicial era una máquina de estados lineal por etapas (saludo → ítems → ¿algo más? → dirección → pago → confirmación) con el LLM extrayendo datos dentro de cada paso.

## Decisión
1. En cada turno el LLM recibe el mensaje completo y el menú, y devuelve una **lista de acciones tipadas** (JSON con schema): `add_item`, `remove_item`, `update_item`, `set_fulfillment`, `set_address`, `set_payment`, `confirm`, `deny`, `cancel_order`, `ask_human`, `ask_question`, `greeting`, `unknown`. Los productos se refieren por el texto del cliente.
2. Un **resolutor determinista** (alias del config + fuzzy matching) traduce texto a IDs del menú; si hay ambigüedad, se pregunta.
3. El **carrito** es el estado; la "etapa" se deriva de qué falta. Una **política** determinista decide la siguiente pregunta.
4. Una **FSM pequeña** modela sólo el ciclo de vida: ORDERING, AWAITING_CONFIRMATION, PENDING_STAFF_CONFIRMATION, CONFIRMED, CANCELLED, HANDOFF, EXPIRED.
5. **Atajos deterministas** antes del LLM (confirmaciones, cancelar, pedir persona, métodos de pago, mensajes no-texto).
6. **Respuestas por plantillas** con tono del config; parafraseo con LLM detrás de flag, apagado por defecto.

## Alternativas consideradas
- **FSM lineal + LLM por etapa**: prompts cortos, pero pierde datos de mensajes multi-dato y obliga a duplicar la lógica de correcciones en cada estado.
- **Agente LLM con tools**: conversación más natural, pero latencia, costo y no determinismo inviables con un 4B local y difíciles de testear.

## Consecuencias
- Positivas: multi-dato y correcciones sin código especial; un solo contrato LLM que se puede evaluar y comparar entre modelos; tests de punta a punta deterministas con un LLM simulado.
- Negativas: el prompt incluye el menú completo (aceptable para menús de 20–40 ítems); el schema de acciones es exigente para un 4B.
- Riesgos: JSON inválido o acciones mal asignadas por el 4B ("una sin cebolla" aplicada a ambas). Mitigación: structured outputs de Ollama, `think=false`, few-shots específicos, un reintento, confirmación con resumen siempre, métricas dedicadas en la evaluación.
- Revisar si: los menús crecen a cientos de ítems (requeriría recuperación de candidatos) o si la evaluación muestra que el 4B no alcanza la exactitud mínima.
