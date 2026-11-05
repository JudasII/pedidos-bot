# CLAUDE.md — pedidos-bot

Motor de asistente conversacional que toma pedidos de hamburgueserías por chat. Proyecto paralelo: validar en meses, o queda como portafolio. Repo **público**.

## Alcance (no negociar sin preguntar al usuario)
- Dentro: motor de conversación y pedidos (español colombiano informal), canal Telegram para desarrollo, LLM detrás de interfaz, notificación al restaurante por Telegram, registro del método de pago, escalamiento a humano (relay vía bot), modo sombra, persistencia/idempotencia, evaluación, observabilidad.
- Fuera: adaptador real de WhatsApp (sólo diseñar la interfaz pensando en él), pasarelas de pago, POS, CRM, panel web, multi-tenant, otros verticales, resumen avanzado de contexto.
- Nunca hardcodear un restaurante en `src/`. Todo lo específico va en `config/restaurants/*.yaml`. Hay un test que lo verifica.
- Si algo es ambiguo, preguntar al usuario en vez de asumir.

## Arquitectura (ADR-0001)
`normalizador → atajos deterministas → NLU (LLM → JSON de acciones) → resolutor de menú → aplicar al carrito → política + FSM de ciclo de vida → plantillas → outbox`.
- El LLM **sólo** produce acciones tipadas referidas por texto del cliente. Nunca IDs ni precios.
- Resolutor: alias del config + fuzzy; ambigüedad ⇒ pregunta de aclaración, nunca adivinar.
- La "etapa" del pedido se deriva de los slots faltantes; la FSM sólo modela el ciclo de vida (ORDERING, AWAITING_CONFIRMATION, PENDING_STAFF_CONFIRMATION, CONFIRMED, CANCELLED, HANDOFF, EXPIRED).
- Respuestas por plantillas; parafraseo con LLM detrás de flag, apagado por defecto.

## Decisiones tomadas con el usuario
- Handoff: relay vía bot (la cajera responde desde el chat del restaurante; `/devolver` o timeout devuelve el control).
- Total = ítems + domicilio fijo por restaurante; opción recoger en local. Sin zonas ni pedido mínimo (por ahora).
- Moneda COP en enteros, formato `$18.000`. Tiempo estimado fijo por modalidad en el config.
- Persistencia: SQLite (WAL) con migraciones SQL propias (ADR-0002).
- Visualización de trazas: Arize Phoenix auto-hospedado vía OTLP.

## Convenciones
- Python 3.12, `uv`. Código en inglés (identificadores); textos al usuario, docs, commits y comentarios de dominio en español.
- Calidad: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest` debe pasar antes de cada commit.
- Tests: unitarios en `tests/unit`, conversaciones de punta a punta con `FakeLLM` en `tests/e2e`, integración con modelo real marcada `@pytest.mark.ollama` (omitida por defecto; correr con `uv run pytest -m ollama`).
- Commits: Conventional Commits en español con referencia a issue, p. ej. `feat(engine): resolutor de menú con alias (#12)`.
- Ramas por hito (`m1-dominio-config`), PR a `main` que cierra issues.
- Decisiones relevantes ⇒ ADR en `docs/adr/NNNN-titulo.md` (plantilla en `0000-plantilla.md`).
- Al cerrar cada hito: `docs/dev-notes/hito-0X.md` y borrador `docs/linkedin/hito-0X.md` (150–250 palabras, primera persona, sólo métricas reales, visual sugerido, pocos hashtags). **Nunca publicar nada**: el usuario revisa y publica.

## Seguridad y datos
- Repo público: secretos sólo en `.env` (ignorado); `.env.example` sin valores.
- Chats reales sólo en `data/private/` (ignorado). Nunca copiarlos a tests, fixtures, docs ni commits.
- Logs y trazas enmascaran teléfonos y direcciones; identificadores de cliente como HMAC con `PII_HASH_SALT`.
- gitleaks corre en pre-commit y CI.
