from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class MovePlan:
    source: Path
    target: Path


def is_non_empty(path: Path) -> bool:
    return path.exists() and any(path.iterdir()) if path.is_dir() else path.exists()


def build_plan(project_root: Path) -> list[MovePlan]:
    workspace = project_root / "workspace"
    return [
        MovePlan(project_root / ".sessions", workspace / "sessions"),
        MovePlan(project_root / "skills", workspace / "skills"),
        MovePlan(project_root / ".agent_memory" / "MEMORY.md", workspace / "MEMORY.md"),
        MovePlan(project_root / ".agent_memory" / "daily", workspace / "memory"),
        MovePlan(project_root / ".agent_memory" / "memory.sqlite3", workspace / "memory.sqlite3"),
    ]


def preflight(plan: list[MovePlan]) -> list[str]:
    errors: list[str] = []
    for item in plan:
        if not item.source.exists():
            continue
        if is_non_empty(item.target):
            errors.append(f"target exists and is not empty: {item.target}")
    return errors


def migrate(plan: list[MovePlan], dry_run: bool) -> None:
    for item in plan:
        if not item.source.exists():
            continue
        print(f"{item.source} -> {item.target}")
        if dry_run:
            continue
        item.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item.source), str(item.target))

    if dry_run:
        return

    legacy_memory = PROJECT_ROOT / ".agent_memory"
    if legacy_memory.exists() and legacy_memory.is_dir() and not any(legacy_memory.iterdir()):
        legacy_memory.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy state into workspace/ for main agent.")
    parser.add_argument("--dry-run", action="store_true", help="show moves without changing files")
    args = parser.parse_args()

    plan = build_plan(PROJECT_ROOT)
    errors = preflight(plan)
    if errors:
        print("Migration aborted:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    migrate(plan, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
