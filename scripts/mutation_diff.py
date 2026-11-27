"""Mutation-test only the source files changed on this branch.

    uv run python scripts/mutation_diff.py [base]     # base defaults to main

Only committed changes count (`base...HEAD`), so a dirty working tree is refused instead of
being silently left out. Files mutmut is told not to mutate are skipped.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

SOURCE = Path("src")


def changed_modules(base: str) -> list[str]:
    raw = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACMRTUXB", f"{base}...HEAD"],
        check=True,
        capture_output=True,
    ).stdout.decode()
    excluded = set(
        tomllib.loads(Path("pyproject.toml").read_text())["tool"]["mutmut"].get("do_not_mutate", [])
    )
    modules = []
    for name in filter(None, raw.split("\0")):
        path = Path(name)
        if path.suffix != ".py" or SOURCE not in path.parents or name in excluded:
            continue
        module = ".".join(path.relative_to(SOURCE).with_suffix("").parts)
        modules.append(module.removesuffix(".__init__"))
    return sorted(set(modules))


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "main"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout
    if dirty:
        print("The working tree has uncommitted changes, which base...HEAD would leave out.")
        print("Commit or stash them first.")
        return 1
    modules = changed_modules(base)
    if not modules:
        print(f"No source files changed against {base}: nothing to mutate.")
        return 0
    print(f"Mutating {len(modules)} module(s) changed against {base}: {', '.join(modules)}")
    return subprocess.run(["mutmut", "run", *(f"{module}.*" for module in modules)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
