"""Crea labels, milestones e issues del proyecto en GitHub usando `gh`.

Idempotente: omite labels, milestones e issues (por título) que ya existen.
Uso: uv run python scripts/github_bootstrap.py [--repo JudasII/pedidos-bot] [--dry-run]

El orden de ISSUES importa: en un repo nuevo, los dos primeros quedan como #1 y #2,
que es lo que referencian los commits de M0.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, field

LABELS: dict[str, tuple[str, str]] = {
    "type:feature": ("1d76db", "Funcionalidad nueva"),
    "type:chore": ("c5def5", "Infraestructura, tooling, mantenimiento"),
    "type:docs": ("0e8a16", "Documentación"),
    "type:eval": ("5319e7", "Evaluación y métricas"),
    "type:bug": ("d73a4a", "Algo no funciona"),
    "area:engine": ("fbca04", "Motor de conversación"),
    "area:domain": ("fef2c0", "Modelos de dominio, carrito, totales"),
    "area:config": ("bfdadc", "Configuración por restaurante"),
    "area:channel": ("f9d0c4", "Canales (Telegram, WhatsApp)"),
    "area:llm": ("d4c5f9", "Proveedores LLM y prompts"),
    "area:storage": ("c2e0c6", "Persistencia e idempotencia"),
    "area:obs": ("e99695", "Observabilidad y costos"),
    "area:eval": ("bfd4f2", "Banco de evaluación"),
    "area:infra": ("ededed", "CI, repo, tooling"),
    "priority:p0": ("b60205", "Bloquea el hito"),
    "priority:p1": ("d93f0b", "Importante"),
    "priority:p2": ("fbca04", "Deseable"),
    "phase:dev": ("0052cc", "Fase 1: desarrollo"),
    "phase:pilot": ("006b75", "Fase 2: piloto WhatsApp + modelo hospedado"),
    "phase:business": ("5e4b8b", "Fase 3: decisión de cobro"),
}

MILESTONES: dict[str, str] = {
    "M0 - Fundaciones": "Repo, tooling, CI, convenciones y ADRs base.",
    "M1 - Dominio y configuración": "Schema YAML, 2 restaurantes, carrito/totales, resolutor de menú.",
    "M2 - Motor con LLM simulado": "Acciones NLU, atajos, política, FSM, plantillas, e2e deterministas.",
    "M3 - Persistencia e idempotencia": "SQLite, dedupe, outbox, expiración/reanudación, reinicio seguro.",
    "M4 - Telegram, restaurante, handoff, sombra": "Canal Telegram, notificación a caja, relay humano, sombra.",
    "M5 - LLM real y observabilidad": "Ollama + Qwen3 4B, OpenTelemetry + Phoenix, costos por pedido.",
    "M6 - Evaluación": "Banco de conversaciones y comparación local vs hospedados.",
    "Fase 2 - Piloto": "WhatsApp Cloud API, despliegue y piloto con 1 restaurante.",
    "Fase 3 - Decisión de cobro": "Medición de valor y decisión de negocio.",
}


@dataclass
class Issue:
    title: str
    milestone: str
    labels: list[str]
    context: str
    criteria: list[str]
    out_of_scope: list[str] = field(default_factory=list)

    def body(self) -> str:
        parts = [f"## Contexto\n{self.context}", "## Criterios de aceptación"]
        parts.append("\n".join(f"- [ ] {c}" for c in self.criteria))
        parts.append("- [ ] Suite completa en verde (ruff, mypy, pytest)")
        if self.out_of_scope:
            parts.append("## Fuera de alcance\n" + "\n".join(f"- {o}" for o in self.out_of_scope))
        return "\n\n".join(parts)


M0, M1, M2, M3 = (
    "M0 - Fundaciones",
    "M1 - Dominio y configuración",
    "M2 - Motor con LLM simulado",
    "M3 - Persistencia e idempotencia",
)
M4, M5, M6 = (
    "M4 - Telegram, restaurante, handoff, sombra",
    "M5 - LLM real y observabilidad",
    "M6 - Evaluación",
)
P2, P3 = "Fase 2 - Piloto", "Fase 3 - Decisión de cobro"

ISSUES: list[Issue] = [
    # M0 — deben quedar como #1 y #2
    Issue(
        "Tooling: uv, ruff, mypy, pytest, pre-commit con gitleaks y CI",
        M0,
        ["type:chore", "area:infra", "priority:p0", "phase:dev"],
        "Base reproducible y guardas para un repo público.",
        [
            "`uv sync` y `uv run pytest` funcionan desde cero",
            "CI en GitHub Actions corre ruff, mypy, pytest y gitleaks",
            "pre-commit bloquea un token falso y cualquier archivo bajo data/private/",
        ],
    ),
    Issue(
        "Docs base: CLAUDE.md, ROADMAP, README, plantillas y ADRs 0001/0002",
        M0,
        ["type:docs", "area:infra", "priority:p0", "phase:dev"],
        "Dejar registradas las decisiones y convenciones antes de escribir lógica.",
        [
            "CLAUDE.md con alcance, arquitectura y convenciones",
            "ROADMAP.md con las 3 fases",
            "ADR-0001 (arquitectura) y ADR-0002 (SQLite) aceptados",
            "Plantillas de issue y PR",
        ],
    ),
    # M1
    Issue(
        "Schema de configuración por restaurante (pydantic + YAML + JSON Schema)",
        M1,
        ["type:feature", "area:config", "priority:p0", "phase:dev"],
        "Menú, categorías, modificadores, reglas, pagos, modalidades, tono y chat del "
        "restaurante en un YAML validado al arrancar.",
        [
            "Config inválido falla con error legible (ruta del campo y motivo)",
            "Referencias cruzadas validadas (grupos de modificadores, categorías en reglas)",
            "JSON Schema exportado a docs/ para edición asistida",
            "IDs de chat y secretos se leen de variables de entorno, no del YAML",
        ],
    ),
    Issue(
        "Configs de ejemplo: burger_demo y minimal_demo",
        M1,
        ["type:feature", "area:config", "priority:p0", "phase:dev"],
        "Hamburguesería completa (3 hamburguesas, tenders, onion flowers, aros, papas, "
        "malteadas, bebidas, cervezas) y un segundo restaurante mínimo para probar que nada "
        "está hardcodeado.",
        [
            "Ambos configs validan",
            "Test que falla si algún nombre/ID de producto de los configs aparece en src/",
            "minimal_demo usa otra modalidad, otros pagos y otro tono",
        ],
    ),
    Issue(
        "Dominio: carrito, modificadores y totales con domicilio fijo / recoger",
        M1,
        ["type:feature", "area:domain", "priority:p0", "phase:dev"],
        "Modelo de pedido determinista; el LLM nunca calcula precios.",
        [
            "Totales correctos con adiciones, quitar ingredientes, tamaños obligatorios y domicilio",
            "Recoger en local no cobra domicilio ni pide dirección",
            "Validación de modificadores requeridos y máximos por línea",
            "Formato COP `$18.000`",
        ],
    ),
    Issue(
        "Resolutor de menú: texto del cliente a IDs (alias + fuzzy)",
        M1,
        ["type:feature", "area:engine", "priority:p0", "phase:dev"],
        "Traduce 'una coca grande', 'hamburgesa sensilla', 'papitas' a IDs del menú.",
        [
            "Tabla de 30+ variantes con errores ortográficos y jerga resuelve correctamente",
            "Ambigüedad o baja confianza devuelve candidatos para aclarar, nunca adivina",
            "Producto inexistente se rechaza explícitamente",
        ],
    ),
    # M2
    Issue(
        "Contrato NLU: schema de acciones e interfaz LLMProvider + FakeLLM",
        M2,
        ["type:feature", "area:llm", "priority:p0", "phase:dev"],
        "Acciones tipadas (add_item, remove_item, update_item, set_fulfillment, set_address, "
        "set_payment, confirm, deny, cancel_order, ask_human, ask_question, greeting, unknown).",
        [
            "JSON Schema de acciones generado desde pydantic",
            "LLMProvider con resultado que incluye uso de tokens y latencia",
            "FakeLLM scriptado y por reglas para tests deterministas",
        ],
    ),
    Issue(
        "Atajos deterministas pre-LLM",
        M2,
        ["type:feature", "area:engine", "priority:p1", "phase:dev"],
        "Confirmaciones, cancelar, pedir persona, métodos de pago y mensajes no-texto sin LLM.",
        [
            "Cobertura de variantes coloquiales ('dale', 'de una', 'listo', 'nequi')",
            "Mensajes de voz, ubicación e imagen producen escalamiento",
            "Métrica: turno marcado como resuelto sin LLM",
        ],
    ),
    Issue(
        "Política y FSM de ciclo de vida",
        M2,
        ["type:feature", "area:engine", "priority:p0", "phase:dev"],
        "Siguiente pregunta según slots faltantes; estados ORDERING, AWAITING_CONFIRMATION, "
        "PENDING_STAFF_CONFIRMATION, CONFIRMED, CANCELLED, HANDOFF, EXPIRED.",
        [
            "Flujo saludo → ítems → ¿algo más? (upsell una vez) → modalidad → dirección → "
            "pago → confirmación con resumen y tiempo estimado",
            "Correcciones en cualquier punto vuelven a ORDERING sin perder datos",
            "Dos 'no entendí' seguidos escalan",
            "Transiciones inválidas imposibles (tests de tabla)",
        ],
    ),
    Issue(
        "Respuestas por plantillas con tono configurable",
        M2,
        ["type:feature", "area:engine", "priority:p1", "phase:dev"],
        "Textos en español colombiano; tono por restaurante; parafraseo LLM detrás de flag.",
        [
            "Resumen de pedido legible con líneas, modificadores, total y ETA",
            "Dos tonos distintos verificados con los dos configs",
            "Botones degradan a lista numerada según capacidades del canal",
        ],
    ),
    Issue(
        "Interfaz de canal + canal en memoria + REPL de desarrollo",
        M2,
        ["type:feature", "area:channel", "priority:p0", "phase:dev"],
        "Interfaz pensada para WhatsApp (ventana 24 h, plantillas, interactivos, voz, "
        "ubicación, imágenes, webhooks duplicados).",
        [
            "InboundMessage/OutboundMessage/ChannelCapabilities definidos",
            "`pedidos-bot chat --restaurant burger_demo` permite conversar en terminal",
            "Canal fake 'tipo WhatsApp' (3 botones, ventana 24 h) usado en tests",
        ],
    ),
    Issue(
        "E2E deterministas de conversación con FakeLLM",
        M2,
        ["type:eval", "area:engine", "priority:p0", "phase:dev"],
        "Escenarios genéricos corridos contra ambos configs.",
        [
            "Flujo feliz, multi-dato, corrección en confirmación, cancelación, pregunta de "
            "menú, pedir persona y no-texto",
            "'2 sencillas, una sin cebolla, y una coca grande' produce 2 líneas distintas + bebida",
        ],
    ),
    # M3
    Issue(
        "SQLite: esquema, migraciones y repositorios",
        M3,
        ["type:feature", "area:storage", "priority:p0", "phase:dev"],
        "Tablas conversations, conversation_state, inbound_messages, turns, llm_calls, orders, "
        "order_items, outbox, handoffs.",
        [
            "Migraciones numeradas aplicadas al arrancar, idempotentes",
            "WAL + foreign keys; bloqueo optimista por versión",
        ],
    ),
    Issue(
        "Idempotencia: dedupe de entrada y pedidos sin duplicar",
        M3,
        ["type:feature", "area:storage", "priority:p0", "phase:dev"],
        "Webhooks duplicados y reintentos no deben crear pedidos dobles.",
        [
            "Mismo external_id procesado una sola vez",
            "'sí' repetido no crea un segundo pedido",
            "Un turno = una transacción",
        ],
    ),
    Issue(
        "Outbox con reintentos y reinicio seguro",
        M3,
        ["type:feature", "area:storage", "priority:p0", "phase:dev"],
        "Envíos a cliente y restaurante confiables aunque el proceso se caiga.",
        [
            "Reintentos exponenciales con dedupe_key",
            "Test que reinicia el servicio a mitad de pedido y continúa",
        ],
    ),
    Issue(
        "Expiración y reanudación de sesiones",
        M3,
        ["type:feature", "area:engine", "priority:p1", "phase:dev"],
        "El cliente puede responder 40 minutos o un día después.",
        [
            "< ventana silenciosa: continúa; < 24 h: pregunta si seguimos; > 24 h o menú "
            "cambiado: nueva conversación con carrito recuperable y precios revalidados",
            "Umbrales configurables por restaurante",
        ],
    ),
    Issue(
        "Logging estructurado con IDs de correlación y enmascarado de PII",
        M3,
        ["type:feature", "area:obs", "priority:p0", "phase:dev"],
        "conversation_id, turn_id, order_id en todos los logs; teléfonos y direcciones enmascarados.",
        [
            "Por turno: mensaje, estado antes/después, acciones, validaciones, latencia, errores",
            "Tests del enmascarador con teléfonos y direcciones colombianas",
            "Identificadores de cliente como HMAC con sal",
        ],
    ),
    # M4
    Issue(
        "Adaptador de Telegram para clientes",
        M4,
        ["type:feature", "area:channel", "priority:p0", "phase:dev"],
        "Long polling con offset persistido; mapeo a InboundMessage.",
        [
            "Texto, voz, ubicación, foto y botones mapeados",
            "update_id usado para dedupe",
        ],
    ),
    Issue(
        "Notificación de pedido al chat del restaurante",
        M4,
        ["type:feature", "area:channel", "priority:p0", "phase:dev"],
        "Resumen limpio listo para que la cajera valide.",
        ["Resumen con código, líneas, modificadores, total, modalidad, dirección y pago"],
    ),
    Issue(
        "Handoff a humano por relay",
        M4,
        ["type:feature", "area:engine", "priority:p0", "phase:dev"],
        "La cajera responde desde el chat del restaurante; el bot reenvía y se pausa.",
        [
            "Reply/`/responder` reenvía al cliente; `/devolver` o timeout devuelve el control",
            "Adjuntos no soportados (voz, ubicación, imagen) se reenvían al restaurante",
        ],
    ),
    Issue(
        "Modo sombra",
        M4,
        ["type:feature", "area:engine", "priority:p0", "phase:dev"],
        "El bot arma el pedido y un humano lo confirma antes de considerarlo válido.",
        [
            "Botones ✓/✗ en el chat del restaurante",
            "Rechazo vuelve a ORDERING o cancela, con aviso al cliente",
            "Demo manual documentada en dev-notes",
        ],
    ),
    # M5
    Issue(
        "OllamaProvider con structured outputs",
        M5,
        ["type:feature", "area:llm", "priority:p0", "phase:dev"],
        "Qwen3:4b local con `format` JSON schema y `think=false`.",
        [
            "Un reintento ante JSON inválido; luego aclaración/escalamiento",
            "Tokens desde prompt_eval_count/eval_count",
            "Tests de integración marcados @pytest.mark.ollama",
        ],
    ),
    Issue(
        "Prompt NLU v1 con few-shots en español colombiano",
        M5,
        ["type:feature", "area:llm", "priority:p0", "phase:dev"],
        "Prompt versionado y compacto con el menú del restaurante.",
        [
            "Few-shots de multi-dato y división de cantidades",
            "Versión del prompt registrada por llamada",
        ],
    ),
    Issue(
        "OpenTelemetry + Phoenix auto-hospedado",
        M5,
        ["type:feature", "area:obs", "priority:p1", "phase:dev"],
        "Spans por turno y llamada LLM con atributos GenAI.",
        [
            "docker-compose con Phoenix",
            "Traza completa de un pedido visible y enmascarada",
        ],
    ),
    Issue(
        "Token economics: llm_calls, tabla de precios y reporte de costos",
        M5,
        ["type:feature", "area:obs", "priority:p0", "phase:dev"],
        "Costo y tokens por turno, pedido y conversación.",
        [
            "config/pricing.yaml editable por modelo",
            "`pedidos-bot report costs` con costo/tokens por pedido (p50/p95)",
        ],
    ),
    # M6
    Issue(
        "Banco de conversaciones sintéticas",
        M6,
        ["type:eval", "area:eval", "priority:p0", "phase:dev"],
        "Errores ortográficos, jerga, cambios de opinión, ambigüedad, multi-dato.",
        ["≥80 casos con pedido esperado", "Cobertura de ambos configs"],
    ),
    Issue(
        "Runner de evaluación, métricas y reporte comparativo",
        M6,
        ["type:eval", "area:eval", "priority:p0", "phase:dev"],
        "Corre el motor real con el modelo elegido.",
        [
            "% completos sin humano, exactitud de ítems y modificadores, escalamiento, "
            "latencia p50/p95, tokens y costo por pedido",
            "Tabla comparativa entre modelos",
        ],
    ),
    Issue(
        "Proveedores hospedados para evaluación (Anthropic, Google, Ollama cloud)",
        M6,
        ["type:feature", "area:llm", "priority:p1", "phase:dev"],
        "Sólo para comparar; mismo contrato LLMProvider.",
        ["Precios en pricing.yaml", "Tests marcados @pytest.mark.hosted"],
    ),
    Issue(
        "Anonimizador de chats reales (data/private, nunca en el repo)",
        M6,
        ["type:eval", "area:eval", "priority:p1", "phase:dev"],
        "Convierte exports de WhatsApp en casos anonimizados para anotar.",
        [
            "Nombres, teléfonos y direcciones reemplazados",
            "Salida sólo bajo data/private/; reportes publican sólo agregados",
        ],
    ),
    Issue(
        "ADR: decisión de modelo para el piloto",
        M6,
        ["type:docs", "area:llm", "priority:p1", "phase:dev"],
        "Con base en la tabla comparativa.",
        ["ADR con métricas reales y costo proyectado por pedido"],
    ),
    # Fase 2 / 3 (backlog)
    Issue(
        "Adaptador WhatsApp Cloud API",
        P2,
        ["type:feature", "area:channel", "priority:p1", "phase:pilot"],
        "Webhooks, verificación de firma, plantillas, ventana de 24 h, interactivos.",
        ["Mismo motor sin cambios", "Dedupe por wamid"],
    ),
    Issue(
        "Despliegue del piloto",
        P2,
        ["type:chore", "area:infra", "priority:p1", "phase:pilot"],
        "Un proceso con SQLite, backups y observabilidad.",
        ["Runbook de despliegue y restauración"],
    ),
    Issue(
        "Piloto en modo sombra con 1 restaurante",
        P2,
        ["type:eval", "area:eval", "priority:p1", "phase:pilot"],
        "Medir en producción real.",
        ["Métricas semanales en dev-notes"],
    ),
    Issue(
        "Criterios de decisión de cobro",
        P3,
        ["type:docs", "priority:p2", "phase:business"],
        "Carga de atención ahorrada, errores, costo por pedido.",
        ["Documento de decisión: cobrar / continuar / archivar como portafolio"],
    ),
]


def gh(*args: str, dry_run: bool = False) -> str:
    if dry_run:
        print("gh", " ".join(args))
        return ""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="JudasII/pedidos-bot")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo, dry = args.repo, args.dry_run

    existing_labels = (
        set()
        if dry
        else {
            lbl["name"]
            for lbl in json.loads(gh("label", "list", "-R", repo, "--json", "name", "-L", "200"))
        }
    )
    for name, (color, desc) in LABELS.items():
        if name not in existing_labels:
            gh(
                "label",
                "create",
                name,
                "-R",
                repo,
                "--color",
                color,
                "--description",
                desc,
                dry_run=dry,
            )

    existing_ms = (
        set()
        if dry
        else {
            m["title"]
            for m in json.loads(gh("api", f"repos/{repo}/milestones?state=all&per_page=100"))
        }
    )
    for title, desc in MILESTONES.items():
        if title not in existing_ms:
            gh(
                "api",
                f"repos/{repo}/milestones",
                "-f",
                f"title={title}",
                "-f",
                f"description={desc}",
                dry_run=dry,
            )

    existing_issues = (
        set()
        if dry
        else {
            i["title"]
            for i in json.loads(
                gh("issue", "list", "-R", repo, "--state", "all", "--json", "title", "-L", "500")
            )
        }
    )
    for issue in ISSUES:
        if issue.title in existing_issues:
            continue
        cmd = [
            "issue",
            "create",
            "-R",
            repo,
            "--title",
            issue.title,
            "--body",
            issue.body(),
            "--milestone",
            issue.milestone,
        ]
        for label in issue.labels:
            cmd += ["--label", label]
        out = gh(*cmd, dry_run=dry)
        if out:
            print(out.strip())


if __name__ == "__main__":
    main()
