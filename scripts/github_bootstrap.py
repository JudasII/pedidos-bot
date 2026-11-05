"""Create (or sync) the project's labels, milestones and issues on GitHub using `gh`.

Idempotent: existing labels, milestones and issues are skipped on create.
With --sync, existing items are updated in place: label descriptions, milestone titles
(matched by creation order) and issue title/body/milestone/labels (issue N == ISSUES[N-1]).

Usage:
    uv run python scripts/github_bootstrap.py [--repo JudasII/pedidos-bot] [--dry-run] [--sync]

Order matters: in a fresh repo the first two issues become #1 and #2, which the M0
commits reference. Never reorder ISSUES or MILESTONES; append new ones at the end.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

LABELS: dict[str, tuple[str, str]] = {
    "type:feature": ("1d76db", "New functionality"),
    "type:chore": ("c5def5", "Infrastructure, tooling, maintenance"),
    "type:docs": ("0e8a16", "Documentation"),
    "type:eval": ("5319e7", "Evaluation and metrics"),
    "type:bug": ("d73a4a", "Something is broken"),
    "area:engine": ("fbca04", "Conversation engine"),
    "area:domain": ("fef2c0", "Domain models, cart, totals"),
    "area:config": ("bfdadc", "Per-restaurant configuration"),
    "area:channel": ("f9d0c4", "Channels (Telegram, WhatsApp)"),
    "area:llm": ("d4c5f9", "LLM providers and prompts"),
    "area:storage": ("c2e0c6", "Persistence and idempotency"),
    "area:obs": ("e99695", "Observability and costs"),
    "area:eval": ("bfd4f2", "Evaluation bank"),
    "area:infra": ("ededed", "CI, repo, tooling"),
    "priority:p0": ("b60205", "Blocks the milestone"),
    "priority:p1": ("d93f0b", "Important"),
    "priority:p2": ("fbca04", "Nice to have"),
    "phase:dev": ("0052cc", "Phase 1: development"),
    "phase:pilot": ("006b75", "Phase 2: WhatsApp pilot + hosted model"),
    "phase:business": ("5e4b8b", "Phase 3: monetization decision"),
}

M0 = "M0 - Foundations"
M1 = "M1 - Domain and configuration"
M2 = "M2 - Engine with simulated LLM"
M3 = "M3 - Persistence and idempotency"
M4 = "M4 - Telegram, restaurant, handoff, shadow mode"
M5 = "M5 - Real LLM and observability"
M6 = "M6 - Evaluation"
P2 = "Phase 2 - Pilot"
P3 = "Phase 3 - Monetization decision"

# Same order the milestones were originally created in (used by --sync).
MILESTONES: dict[str, str] = {
    M0: "Repo, tooling, CI, conventions and base ADRs.",
    M1: "YAML schema, 2 restaurants, cart/totals, menu resolver.",
    M2: "NLU actions, shortcuts, policy, FSM, templates, deterministic e2e tests.",
    M3: "PostgreSQL, dedupe, outbox, expiry/resumption, safe restarts.",
    M4: "Telegram channel, cashier notification, human relay, shadow mode.",
    M5: "Ollama + Qwen3 4B, OpenTelemetry + Langfuse, cost per order.",
    M6: "Conversation bank and local vs hosted comparison.",
    P2: "WhatsApp Cloud API, deployment and pilot with 1 restaurant.",
    P3: "Value measurement and business decision.",
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
        parts = [f"## Context\n{self.context}", "## Acceptance criteria"]
        parts.append("\n".join(f"- [ ] {c}" for c in self.criteria))
        parts.append("- [ ] Full suite green (ruff, mypy, pytest)")
        if self.out_of_scope:
            parts.append("## Out of scope\n" + "\n".join(f"- {o}" for o in self.out_of_scope))
        return "\n\n".join(parts)


ISSUES: list[Issue] = [
    # M0 — must stay #1 and #2
    Issue(
        "Tooling: uv, ruff, mypy, pytest, pre-commit with gitleaks and CI",
        M0,
        ["type:chore", "area:infra", "priority:p0", "phase:dev"],
        "Reproducible base and guards for a public repo.",
        [
            "`uv sync` and `uv run pytest` work from scratch",
            "GitHub Actions CI runs ruff, mypy, pytest and gitleaks",
            "pre-commit blocks a fake token and any file under data/private/",
        ],
    ),
    Issue(
        "Base docs: CLAUDE.md, ROADMAP, README, templates and ADRs 0001-0003",
        M0,
        ["type:docs", "area:infra", "priority:p0", "phase:dev"],
        "Record decisions and conventions before writing logic. English is the repo's working language.",
        [
            "CLAUDE.md with scope, architecture, language policy and conventions",
            "ROADMAP.md with the 3 phases",
            "ADR-0001 (architecture), ADR-0002 (PostgreSQL) and ADR-0003 (Langfuse) accepted",
            "Issue and PR templates",
        ],
    ),
    # M1
    Issue(
        "Per-restaurant configuration schema (pydantic + YAML + JSON Schema)",
        M1,
        ["type:feature", "area:config", "priority:p0", "phase:dev"],
        "Menu, categories, modifiers, rules, payment methods, fulfillment modes, tone and "
        "restaurant chat in a YAML file validated at startup.",
        [
            "An invalid config fails with a readable error (field path and reason)",
            "Cross-references validated (modifier groups, categories used in rules)",
            "JSON Schema exported to docs/ for assisted editing",
            "Chat IDs and secrets are read from environment variables, not from the YAML",
        ],
    ),
    Issue(
        "Sample configs: burger_demo and minimal_demo",
        M1,
        ["type:feature", "area:config", "priority:p0", "phase:dev"],
        "A full burger restaurant (3 burgers, chicken tenders, onion flowers, onion rings, fries, "
        "milkshakes, drinks, beers) and a second minimal restaurant to prove nothing is hardcoded.",
        [
            "Both configs validate",
            "A test fails if any product name/ID from the configs appears in src/",
            "minimal_demo uses a different fulfillment mode, payment methods and tone",
        ],
    ),
    Issue(
        "Domain: cart, modifiers and totals with fixed delivery fee / pickup",
        M1,
        ["type:feature", "area:domain", "priority:p0", "phase:dev"],
        "Deterministic order model; the LLM never computes prices.",
        [
            "Correct totals with add-ons, removed ingredients, required sizes and delivery fee",
            "Pickup charges no delivery fee and asks for no address",
            "Validation of required modifiers and per-line maximums",
            "COP formatting `$18.000`",
        ],
    ),
    Issue(
        "Menu resolver: customer text to menu IDs (aliases + fuzzy)",
        M1,
        ["type:feature", "area:engine", "priority:p0", "phase:dev"],
        "Maps 'una coca grande', 'hamburgesa sensilla', 'papitas' to menu IDs.",
        [
            "A table of 30+ variants with typos and slang resolves correctly",
            "Ambiguity or low confidence returns candidates to clarify; it never guesses",
            "Non-existent products are explicitly rejected",
        ],
    ),
    # M2
    Issue(
        "NLU contract: action schema, LLMProvider interface and FakeLLM",
        M2,
        ["type:feature", "area:llm", "priority:p0", "phase:dev"],
        "Typed actions (add_item, remove_item, update_item, set_fulfillment, set_address, "
        "set_payment, confirm, deny, cancel_order, ask_human, ask_question, greeting, unknown).",
        [
            "Action JSON Schema generated from pydantic",
            "LLMProvider result includes token usage and latency",
            "Scripted and rule-based FakeLLM for deterministic tests",
        ],
    ),
    Issue(
        "Deterministic pre-LLM shortcuts",
        M2,
        ["type:feature", "area:engine", "priority:p1", "phase:dev"],
        "Confirmations, cancel, ask for a person, payment methods and non-text messages without the LLM.",
        [
            "Covers colloquial variants ('dale', 'de una', 'listo', 'nequi')",
            "Voice, location and image messages trigger a handoff",
            "Metric: turn flagged as resolved without the LLM",
        ],
    ),
    Issue(
        "Policy and lifecycle FSM",
        M2,
        ["type:feature", "area:engine", "priority:p0", "phase:dev"],
        "Next question from missing slots; states ORDERING, AWAITING_CONFIRMATION, "
        "PENDING_STAFF_CONFIRMATION, CONFIRMED, CANCELLED, HANDOFF, EXPIRED.",
        [
            "Flow greeting → items → anything else? (upsell once) → fulfillment → address → "
            "payment → confirmation with summary and ETA",
            "Corrections at any point return to ORDERING without losing data",
            "Two consecutive 'didn't understand' turns escalate",
            "Invalid transitions are impossible (table-driven tests)",
        ],
    ),
    Issue(
        "Template responses with configurable tone",
        M2,
        ["type:feature", "area:engine", "priority:p1", "phase:dev"],
        "Customer-facing text in Colombian Spanish; per-restaurant tone; LLM paraphrasing behind a flag.",
        [
            "Readable order summary with lines, modifiers, total and ETA",
            "Two different tones verified with the two configs",
            "Buttons degrade to a numbered list according to channel capabilities",
        ],
    ),
    Issue(
        "Channel interface + in-memory channel + development REPL",
        M2,
        ["type:feature", "area:channel", "priority:p0", "phase:dev"],
        "Interface designed for WhatsApp (24-hour window, templates, interactive messages, voice, "
        "locations, images, duplicate webhooks).",
        [
            "InboundMessage/OutboundMessage/ChannelCapabilities defined",
            "`pedidos-bot chat --restaurant burger_demo` allows chatting in the terminal",
            "A fake 'WhatsApp-like' channel (3 buttons, 24-hour window) used in tests",
        ],
    ),
    Issue(
        "Deterministic end-to-end conversation tests with FakeLLM",
        M2,
        ["type:eval", "area:engine", "priority:p0", "phase:dev"],
        "Generic scenarios run against both configs.",
        [
            "Happy path, multi-field message, correction at confirmation, cancellation, menu "
            "question, ask for a person and non-text message",
            "'2 sencillas, una sin cebolla, y una coca grande' yields 2 distinct lines + a drink",
        ],
    ),
    # M3
    Issue(
        "PostgreSQL: schema, migrations and repositories",
        M3,
        ["type:feature", "area:storage", "priority:p0", "phase:dev"],
        "Tables conversations, conversation_state, inbound_messages, turns, llm_calls, orders, "
        "order_items, outbox, handoffs. psycopg 3, plain SQL migrations, Docker Compose for local dev.",
        [
            "Numbered migrations applied at startup, idempotent",
            "`docker compose up db` provides a local database; CI runs repository tests against a Postgres service",
            "Row locking per conversation (`SELECT ... FOR UPDATE`)",
        ],
    ),
    Issue(
        "Idempotency: inbound dedupe and no duplicate orders",
        M3,
        ["type:feature", "area:storage", "priority:p0", "phase:dev"],
        "Duplicate webhooks and retries must not create duplicate orders.",
        [
            "The same external_id is processed only once",
            "A repeated 'sí' does not create a second order",
            "One turn = one transaction",
        ],
    ),
    Issue(
        "Outbox with retries and safe restarts",
        M3,
        ["type:feature", "area:storage", "priority:p0", "phase:dev"],
        "Reliable delivery to customer and restaurant even if the process crashes.",
        [
            "Exponential retries with dedupe_key; workers claim rows with FOR UPDATE SKIP LOCKED",
            "A test restarts the service mid-order and the order continues",
        ],
    ),
    Issue(
        "Session expiry and resumption",
        M3,
        ["type:feature", "area:engine", "priority:p1", "phase:dev"],
        "The customer may reply 40 minutes or a day later.",
        [
            "< silent window: continue; < 24 h: ask whether to continue; > 24 h or menu "
            "changed: new conversation with a recoverable cart and revalidated prices",
            "Thresholds configurable per restaurant",
        ],
    ),
    Issue(
        "Structured logging with correlation IDs and PII masking",
        M3,
        ["type:feature", "area:obs", "priority:p0", "phase:dev"],
        "conversation_id, turn_id, order_id in every log line; phones and addresses masked.",
        [
            "Per turn: message, state before/after, actions, validations, latency, errors",
            "Masker tests with Colombian phone numbers and addresses",
            "Customer identifiers as salted HMAC",
        ],
    ),
    # M4
    Issue(
        "Telegram adapter for customers",
        M4,
        ["type:feature", "area:channel", "priority:p0", "phase:dev"],
        "Long polling with persisted offset; mapping to InboundMessage.",
        [
            "Text, voice, location, photo and buttons mapped",
            "update_id used for dedupe",
        ],
    ),
    Issue(
        "Order notification to the restaurant chat",
        M4,
        ["type:feature", "area:channel", "priority:p0", "phase:dev"],
        "A clean summary ready for the cashier to validate.",
        ["Summary with code, lines, modifiers, total, fulfillment, address and payment method"],
    ),
    Issue(
        "Human handoff via relay",
        M4,
        ["type:feature", "area:engine", "priority:p0", "phase:dev"],
        "The cashier replies from the restaurant chat; the bot forwards and pauses.",
        [
            "Reply/`/responder` forwards to the customer; `/devolver` or timeout returns control",
            "Unsupported attachments (voice, location, image) are forwarded to the restaurant",
        ],
    ),
    Issue(
        "Shadow mode",
        M4,
        ["type:feature", "area:engine", "priority:p0", "phase:dev"],
        "The bot builds the order and a human must confirm it before it counts as valid.",
        [
            "✓/✗ buttons in the restaurant chat",
            "Rejection returns to ORDERING or cancels, notifying the customer",
            "Manual demo documented in dev notes",
        ],
    ),
    # M5
    Issue(
        "OllamaProvider with structured outputs",
        M5,
        ["type:feature", "area:llm", "priority:p0", "phase:dev"],
        "Local Qwen3:4b with a JSON-schema `format` and `think=false`.",
        [
            "One retry on invalid JSON; then clarification/handoff",
            "Tokens from prompt_eval_count/eval_count",
            "Integration tests marked @pytest.mark.ollama",
        ],
    ),
    Issue(
        "NLU prompt v1 with Colombian Spanish few-shots",
        M5,
        ["type:feature", "area:llm", "priority:p0", "phase:dev"],
        "Versioned, compact prompt including the restaurant's menu.",
        [
            "Few-shots for multi-field messages and quantity splitting",
            "Prompt version recorded on every call (Langfuse prompt management)",
        ],
    ),
    Issue(
        "OpenTelemetry + self-hosted Langfuse",
        M5,
        ["type:feature", "area:obs", "priority:p1", "phase:dev"],
        "Spans per turn and per LLM call with GenAI attributes, exported via OTLP to Langfuse.",
        [
            "docker-compose profile with self-hosted Langfuse",
            "A full order trace is visible in Langfuse with PII masked",
        ],
    ),
    Issue(
        "Token economics: llm_calls, pricing table and cost report",
        M5,
        ["type:feature", "area:obs", "priority:p0", "phase:dev"],
        "Cost and tokens per turn, order and conversation.",
        [
            "Editable per-model config/pricing.yaml",
            "`pedidos-bot report costs` with cost/tokens per order (p50/p95)",
        ],
    ),
    # M6
    Issue(
        "Synthetic conversation bank",
        M6,
        ["type:eval", "area:eval", "priority:p0", "phase:dev"],
        "Typos, slang, changes of mind, ambiguity, multi-field messages (conversations in Spanish).",
        ["≥80 cases with expected order", "Coverage of both configs"],
    ),
    Issue(
        "Evaluation runner, metrics and comparison report",
        M6,
        ["type:eval", "area:eval", "priority:p0", "phase:dev"],
        "Runs the real engine with the selected model; results also pushed to Langfuse datasets.",
        [
            "% completed without a human, item and modifier accuracy, escalation rate, "
            "p50/p95 latency, tokens and cost per order",
            "Comparison table across models",
        ],
    ),
    Issue(
        "Hosted providers for evaluation (Anthropic, Google, Ollama cloud)",
        M6,
        ["type:feature", "area:llm", "priority:p1", "phase:dev"],
        "For comparison only; same LLMProvider contract.",
        ["Prices in pricing.yaml", "Tests marked @pytest.mark.hosted"],
    ),
    Issue(
        "Real chat anonymizer (data/private, never in the repo)",
        M6,
        ["type:eval", "area:eval", "priority:p1", "phase:dev"],
        "Turns WhatsApp exports into anonymized cases for annotation.",
        [
            "Names, phone numbers and addresses replaced",
            "Output only under data/private/; reports publish aggregates only",
        ],
    ),
    Issue(
        "ADR: model choice for the pilot",
        M6,
        ["type:docs", "area:llm", "priority:p1", "phase:dev"],
        "Based on the comparison table.",
        ["ADR with real metrics and projected cost per order"],
    ),
    # Phase 2 / 3 (backlog)
    Issue(
        "WhatsApp Cloud API adapter",
        P2,
        ["type:feature", "area:channel", "priority:p1", "phase:pilot"],
        "Webhooks, signature verification, templates, 24-hour window, interactive messages.",
        ["Same engine, no changes", "Dedupe by wamid"],
    ),
    Issue(
        "Pilot deployment",
        P2,
        ["type:chore", "area:infra", "priority:p1", "phase:pilot"],
        "PostgreSQL with backups, Langfuse and the bot process; add a cache only if a need appears.",
        ["Deployment and restore runbook"],
    ),
    Issue(
        "Shadow-mode pilot with 1 restaurant",
        P2,
        ["type:eval", "area:eval", "priority:p1", "phase:pilot"],
        "Measure in real production.",
        ["Weekly metrics in dev notes"],
    ),
    Issue(
        "Monetization decision criteria",
        P3,
        ["type:docs", "priority:p2", "phase:business"],
        "Attention workload saved, errors, cost per order.",
        ["Decision document: charge / continue / archive as a portfolio piece"],
    ),
]


def gh(*args: str, dry_run: bool = False) -> str:
    if dry_run:
        print("gh", " ".join(args[:4]), "...")
        return ""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def ensure_labels(repo: str, dry: bool, sync: bool) -> None:
    existing: set[str] = set()
    if not dry:
        raw = gh("label", "list", "-R", repo, "--json", "name", "-L", "200")
        existing = {lbl["name"] for lbl in json.loads(raw)}
    for name, (color, desc) in LABELS.items():
        if name not in existing:
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
        elif sync:
            gh(
                "label",
                "edit",
                name,
                "-R",
                repo,
                "--color",
                color,
                "--description",
                desc,
                dry_run=dry,
            )


def ensure_milestones(repo: str, dry: bool, sync: bool) -> None:
    existing: list[dict[str, Any]] = []
    if not dry:
        existing = json.loads(gh("api", f"repos/{repo}/milestones?state=all&per_page=100"))
        existing.sort(key=lambda m: int(m["number"]))
    if sync and existing:
        for ms, (title, desc) in zip(existing, MILESTONES.items(), strict=False):
            gh(
                "api",
                "-X",
                "PATCH",
                f"repos/{repo}/milestones/{ms['number']}",
                "-f",
                f"title={title}",
                "-f",
                f"description={desc}",
                dry_run=dry,
            )
        return
    titles = {m["title"] for m in existing}
    for title, desc in MILESTONES.items():
        if title not in titles:
            gh(
                "api",
                f"repos/{repo}/milestones",
                "-f",
                f"title={title}",
                "-f",
                f"description={desc}",
                dry_run=dry,
            )


def ensure_issues(repo: str, dry: bool, sync: bool) -> None:
    existing: dict[int, dict[str, Any]] = {}
    if not dry:
        raw = gh(
            "issue",
            "list",
            "-R",
            repo,
            "--state",
            "all",
            "--json",
            "number,title,labels",
            "-L",
            "500",
        )
        existing = {int(i["number"]): i for i in json.loads(raw)}
    titles = {i["title"] for i in existing.values()}
    for number, issue in enumerate(ISSUES, start=1):
        if sync and number in existing:
            current = {lbl["name"] for lbl in existing[number]["labels"]}
            cmd = [
                "issue",
                "edit",
                str(number),
                "-R",
                repo,
                "--title",
                issue.title,
                "--body",
                issue.body(),
                "--milestone",
                issue.milestone,
            ]
            for label in sorted(set(issue.labels) - current):
                cmd += ["--add-label", label]
            for label in sorted(current - set(issue.labels)):
                cmd += ["--remove-label", label]
            gh(*cmd, dry_run=dry)
            print(f"synced #{number}: {issue.title}")
            continue
        if issue.title in titles:
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="JudasII/pedidos-bot")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sync", action="store_true", help="update existing items in place")
    args = parser.parse_args()
    ensure_labels(args.repo, args.dry_run, args.sync)
    ensure_milestones(args.repo, args.dry_run, args.sync)
    ensure_issues(args.repo, args.dry_run, args.sync)


if __name__ == "__main__":
    main()
