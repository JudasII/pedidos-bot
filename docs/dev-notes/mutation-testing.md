# Mutation testing (2026-11-26)

Mutation testing asks whether the tests would catch a bug: it makes one small change to the source at a time (a `<` becomes `<=`, a string becomes `"XX…XX"`, an argument becomes `None`) and runs the tests. A change the tests catch is *killed*; one they don't is a *survivor*, a bug the suite would let through.

The process follows the `mutation-testing` skill (written for Stryker in JavaScript): one full baseline run, scoped re-runs of the modules whose tests were strengthened, and every survivor classified. Python has no Stryker, so the tool is **mutmut 3.8.1**.

## How to run it

```bash
uv run mutmut run                                   # the whole package (~4 minutes)
uv run mutmut run "pedidos_bot.engine.turn.*"        # one module
uv run python scripts/mutation_diff.py main          # only modules changed against main
uv run mutmut results                                # what survived
uv run mutmut show <mutant name>                     # the change it made
```

Configuration is under `[tool.mutmut]` in `pyproject.toml`. `storage/postgres.py` is left out, because its tests need a real database that parallel mutants can't share. `test_repo_hygiene.py` is skipped inside mutmut's copy, which has no git history. Results live in `mutants/`, which is git-ignored.

Don't run the whole package after every change: it is the end-of-phase check, not part of the test-first loop.

## Results

| | Mutants | Killed | Survived | No test | Score |
|---|---:|---:|---:|---:|---:|
| Baseline | 6,283 | 5,384 | 824 | 75 | 85.7 % (overstated, see below) |
| Now | 6,260 | 5,683 | 524 | 53 | **90.8 %** |

By module, for the modules whose tests were strengthened and re-run (survivors and untested together):

| Module | Before | Now | Score now |
|---|---:|---:|---:|
| `engine/service.py` | 145 | 26 | 97.4 % |
| `engine/turn.py` | 102 | 29 | 97.1 % |
| `channels/telegram.py` | 89 | 38 | 93.1 % |
| `chats/transcripts.py` | 61 | 31 | 89.3 % |
| `engine/staff.py` | 33 | 10 | 95.4 % |
| `domain/cart.py` | 26 | 18 | 95.6 % |
| `config/loader.py` | 16 | 8 | 90.2 % |
| `observability/logging.py` | 15 | 12 | 80.6 % |
| `chats/whatsapp.py` | 15 | 7 | 95.8 % |
| `engine/responder.py` | 7 | 10 | 93.0 % |
| `engine/lifecycle.py` | 4 | 3 | 90.9 % |
| `domain/order.py` | 1 | 0 | 100 % |

**The baseline overstated the score.** `test_two_burgers_one_different_and_a_drink[scripted]` built its `ScriptedLLM` once, at import time. Inside mutmut the suite runs more than once in one process, so the script was already used up and the test failed for *every* mutant it ran against. Those mutants were counted as killed without any test detecting them. Once the test was fixed, a few dozen real survivors appeared, for example the blank line between the parts of a reply; they are killed now. The modules that were not re-run (`cli.py`, `app.py`, `engine/resolver.py`, `llm/fake.py`, `engine/shortcuts.py`, `channels/terminal.py`) keep their baseline numbers, which may be overstated in the same way. A fresh full run would measure them; the skill allows two full runs per phase, and both were spent (the second one aborted on that test).

## What it found besides weak tests

- **A real bug:** "una hamburguesa sin cebolla" asked which burger and then lost "sin cebolla". Options the model sends separately were lost the same way. The words left after a name several products share are now kept for whichever product is picked.
- **A fragile test,** the one above.
- **Two dead steps,** which only produced equivalent mutants and were removed: keeping a revised order's id (its id is the conversation's now) and clearing the pending question on a staff decision (the confirming turn already clears it).
- **Two checks in the test harness,** applied to every end-to-end turn: the conversation must survive a restart (serialize and load back equal), and every outbound message must leave under its own key starting with the inbound message's id.

## The survivors that remain, classified

Every survivor ends as one of three things: killed by a test, **equivalent** (no input makes it behave differently), or **deferred** (a judgment call, named here).

### Modules re-run (196 at the last count)

- **Equivalent, about 95.**
  - *Values that read the same:* `None` where `False` is set and only tested for truth, encoding names (`"UTF-8"` is `"utf-8"`), JSON indentation, a random salt of another length, a digest one character longer.
  - *Unreachable branches*, for example:
    - the cash answer when no payment is set (the question comes only after a payment);
    - a required choice without `asked_choice` (they are always set together);
    - `or True` on a lookup that never misses, since products, groups and payment methods come from the validated config;
    - a staff decision on an order with a channel but no chat (orders have both or neither).
  - *State overwritten in the same turn:* `change_asked`, `options` and `asked` are set again by the reply before the turn ends; `_understood` is irrelevant whenever `_final` is set.
  - *Masking names:* the order of name alternatives (every name is matched with `\b`, so order can't change the result).
  - *Export fields:* `rsplit(".")` variants (the attachment pattern guarantees exactly one dot is split), `raw` and `customer_ref` in a private Telegram chat (sender and chat share the id), `can_send_freeform` on Telegram (nothing calls it yet).
- **Deferred, about 100:**
  - *Log wording and fields, 9:* the logs that matter (turns, staff actions, handoffs, Telegram failures) are pinned; the rest are wording.
  - *Exception messages, 16:* only the type reaches the logs, and nothing shows the message to anyone.
  - *Telegram retry timing and shutdown, 17:* backoff multipliers, `retry_after` on a failed poll, waiting for acknowledgements at shutdown. They can only be observed as wall-clock waits; a fake clock in the adapter would make them testable.
  - *Race guards in `expire_handoffs`, 3:* they only matter when another worker or a staff message changes the chat between listing it and locking it.
  - *Latency arithmetic, 3* (`latency_ms`).
  - *Upper-case folder names, 2* (`"RAW"`, `"MASKED"`): equivalent on macOS, whose disk ignores case; CI on Linux would see them.
  - *`SequenceMatcher(autojunk=…)`:* it changes how two snapshots align only on long chats with very repeated lines; a 300-line test didn't separate them.
  - *Logging configuration, 12:* mutmut reports these as surviving although `tests/unit/observability/test_logging.py` asserts the behavior (level filtering, UTC timestamps). Probably structlog's global configuration and mutmut's process model; not investigated further.
  - *`_find_line` with two different products:* the "which line?" message names the first one. What it should say for mixed products is a product decision.
- **Noticed, not changed:** joining snapshots sorts what differs by time. That is fine for real exports, whose times only move forward, but it would reorder messages if a phone's clock jumped back.

### Modules not re-run (385)

- **`cli.py`, 255:** mostly argparse help texts and the wording of printed messages. The commands themselves are tested.
- **`app.py`, 78:** `run_telegram` has no test (41 mutants with no test at all). It wires the real process (signals, the httpx client, PostgreSQL), and the manual Telegram runs cover it. The rest are the delivery and handoff loops (intervals, error logging).
- **`engine/resolver.py`, 38:** alternative-reading internals. They mostly change fields of `Match` that no caller reads (`query`, `method`, a candidate's `score`), or modifier matches the turn now re-derives from the words alone.
- **`llm/fake.py`, 10:** the stand-in model that M5 replaces.
- **`engine/shortcuts.py`, 2:** code that runs at import, before mutmut switches a mutant on, so no test can catch it under this tool.
- **`channels/terminal.py`, 2:** REPL output.

These are deferred, not equivalent: the baseline caveat above applies to them, and a fresh full run is the way to re-measure them.
