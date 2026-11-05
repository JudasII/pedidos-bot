# ADR-0002: SQLite como persistencia de la fase de desarrollo y piloto

- Estado: aceptada
- Fecha: 2026-11-04

## Contexto
Se necesita persistir estado de conversación, pedidos, mensajes entrantes (para idempotencia), un outbox de envíos y métricas de LLM desde la primera versión, con reinicios seguros. El alcance es de 2–3 restaurantes en un solo proceso.

## Decisión
SQLite en modo WAL con `foreign_keys=ON`, acceso vía `sqlite3` de la librería estándar y migraciones SQL numeradas propias. Un turno se procesa en una transacción `BEGIN IMMEDIATE`; el estado de conversación usa bloqueo optimista por `version`; la deduplicación usa restricciones `UNIQUE` (`inbound_messages(channel, external_id)`, `orders.idempotency_key`, `outbox.dedupe_key`).

## Alternativas consideradas
- **PostgreSQL**: más robusto en concurrencia, pero agrega infraestructura sin necesidad para el volumen actual.
- **ORM (SQLAlchemy) + Alembic**: más abstracción y dependencias de las que el modelo de datos pequeño justifica.
- **Redis para sesiones**: no da durabilidad transaccional junto con los pedidos.

## Consecuencias
- Positivas: cero infraestructura, backups como copia de archivo, transacciones reales para la idempotencia.
- Negativas: un solo escritor; no apto para varios procesos/hosts.
- Revisar si: se requiere más de un proceso, más de ~10 restaurantes o despliegue en varias réplicas ⇒ migrar a PostgreSQL (el SQL se mantiene portable).
