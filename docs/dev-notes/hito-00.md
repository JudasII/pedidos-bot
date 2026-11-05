# Hito M0 — Fundaciones

Fecha: 2026-11-04 · PR #35 · Issues #1, #2

## Qué se hizo
- Repo nuevo `pedidos-bot` (público) con uv + Python 3.12, ruff, mypy strict, pytest.
- Guardas de repo público: gitleaks y bloqueo de `data/private/` en pre-commit y CI; tests que verifican que `.env`, datos reales, DB y reportes están ignorados.
- CLAUDE.md, ROADMAP con 3 fases, ADR-0001 (arquitectura) y ADR-0002 (SQLite).
- Script idempotente que creó 20 labels, 9 milestones y 34 issues con criterios de aceptación.

## Decisiones
- Arquitectura: LLM como NLU de acciones + carrito como estado + política determinista, en vez de FSM lineal por etapas (ADR-0001).
- Handoff por relay vía bot; domicilio fijo + recoger en local; COP en enteros.
- SQLite + migraciones SQL propias, sin ORM (ADR-0002).

## Alternativas descartadas
- FSM lineal con prompt por etapa: pierde datos en mensajes multi-dato.
- Agente LLM con tools: inviable en latencia/determinismo con un modelo de 4B.
- Langfuse para trazas: demasiada infraestructura para un proyecto paralelo; se usará Phoenix.

## Problemas
- El hook de gitleaks fijado en v8.21.2 no detectó un token falso de Telegram que la v8.30.1 sí detecta: se subió la versión y se verificó el bloqueo.
- Un token falso con forma de API key de Anthropic no fue detectado por ninguna versión; no se asume cobertura para ese formato.

## Métricas reales
- 11 tests, 0 fallos. 34 issues planificados en 9 milestones.

## Aprendizajes
- Verificar las guardas con un caso positivo (un secreto falso) en vez de asumir que el hook funciona.
