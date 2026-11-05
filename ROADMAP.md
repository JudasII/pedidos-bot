# Roadmap

Seguimiento detallado en los [milestones e issues de GitHub](https://github.com/JudasII/pedidos-bot/milestones).

## Fase 1 — Desarrollo (actual)

| Hito | Objetivo | Estado |
|---|---|---|
| M0 - Fundaciones | Repo, tooling, CI, convenciones, ADRs base | en revisión (PR #35) |
| M1 - Dominio y configuración | Schema YAML validado, 2 restaurantes de ejemplo, carrito y totales, resolutor de menú | pendiente |
| M2 - Motor con LLM simulado | Contrato de acciones, atajos, política, FSM, plantillas, REPL; e2e deterministas | pendiente |
| M3 - Persistencia e idempotencia | SQLite, dedupe, outbox, expiración/reanudación, reinicio seguro, logging con IDs | pendiente |
| M4 - Telegram, restaurante, handoff, sombra | Adaptador Telegram, notificación a caja, relay humano, modo sombra | pendiente |
| M5 - LLM real y observabilidad | Ollama + Qwen3 4B, OpenTelemetry + Phoenix, costos y tokens por pedido | pendiente |
| M6 - Evaluación | Banco de conversaciones, comparación local vs hospedados, ADR de modelo | pendiente |

## Fase 2 — Piloto con WhatsApp y modelo hospedado

- Adaptador WhatsApp Cloud API (webhooks, plantillas, ventana de 24 h, mensajes interactivos).
- Despliegue de un solo proceso con SQLite y backups.
- Piloto en modo sombra con 1 restaurante; luego modo autónomo si las métricas lo permiten.

## Fase 3 — Decisión de cobro

- Medir carga de atención ahorrada y errores de pedido durante el piloto.
- Definir criterios de continuar/cobrar/archivar como portafolio.
