# Borrador LinkedIn — Hito 0 (no publicado; revisar antes)

Empecé un proyecto paralelo: un asistente que toma pedidos de hamburgueserías por chat. En muchos negocios de barrio el 100 % de los pedidos llega por WhatsApp y quien responde es la misma persona que atiende la caja.

Antes de escribir una línea de lógica, tomé la decisión más importante: cómo repartir el trabajo entre el modelo de lenguaje y el código.

La idea obvia era una máquina de estados por etapas (ítems → dirección → pago) con el LLM extrayendo datos en cada paso. El problema: la gente escribe "2 sencillas, una sin cebolla, una coca grande, a la calle 10, pago en efectivo" en un solo mensaje. Una etapa que solo espera ítems pierde el resto.

Me quedé con otro diseño: el LLM solo traduce el mensaje a una lista de acciones (agregar ítem, poner dirección, método de pago...), y el código valida contra el menú, calcula el total y decide qué preguntar. El modelo nunca inventa productos ni precios.

Otro aprendizaje de este primer hito: probé el escáner de secretos con un token falso y la versión que había fijado no lo detectaba. Si no lo hubiera probado, habría asumido que estaba protegido.

Resultado del hito 0: repo público con CI, guardas para secretos y datos reales, 2 decisiones de arquitectura documentadas y 34 issues planificados en 9 hitos.

Visual sugerido: diagrama del flujo mensaje → acciones → validación → respuesta.

#IA #Python #LLM
