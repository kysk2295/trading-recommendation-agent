#!/usr/bin/env -S uv run --offline --script
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from development_harness.grok_task_runner import GrokTaskRunnerError, prepare_grok_task, run_grok_task
from development_harness.task_contract import GrokTaskContract

_MAX_CONTRACT_BYTES = 64 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded Grok development-task harness")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--worktree-root", type=Path, required=True)
    parser.add_argument("--grok-binary", default="grok")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _load_contract(path: Path) -> GrokTaskContract:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_CONTRACT_BYTES:
            raise ValueError
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        return GrokTaskContract.model_validate(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("invalid Grok task request") from None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        contract = _load_contract(args.contract)
        plan = prepare_grok_task(
            contract,
            repo=Path.cwd(),
            worktree_root=args.worktree_root,
            grok_binary=args.grok_binary,
            dry_run=args.dry_run,
        )
        report = run_grok_task(plan, dry_run=args.dry_run)
    except (GrokTaskRunnerError, ValueError):
        print(json.dumps({"status": "rejected", "message": "invalid Grok task request"}), file=sys.stderr)
        return 1
    print(json.dumps(report.as_safe_dict(), separators=(",", ":"), sort_keys=True))
    return 0 if report.status in {"planned", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
