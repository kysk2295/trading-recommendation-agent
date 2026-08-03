#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run run_research_agent_backup.py --help
# 3. Or make executable and run:
#      chmod +x run_research_agent_backup.py && ./run_research_agent_backup.py --help
# ──────────────────

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import TypedDict, assert_never

from trading_agent.research_agent_backup import create_backup, verify_restore
from trading_agent.research_agent_backup_models import (
    BackupError,
    BackupLimits,
    BackupRequest,
    BackupResult,
    RestoreRequest,
)


class _Command(StrEnum):
    BACKUP = "backup"
    VERIFY_RESTORE = "verify-restore"


class _Summary(TypedDict):
    artifact_count: int
    broker_mutation: int
    heavy_processes: int
    manifest_sha256: str
    model_calls: int
    operation: str
    provider_calls: int
    status: str
    total_bytes: int


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or verify-restore a bounded private research-agent backup.")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="Snapshot two stores and two receipt roots into a new bundle.")
    backup.add_argument("--cycle-db", required=True, type=Path)
    backup.add_argument("--hermes-db", required=True, type=Path)
    backup.add_argument("--cycle-receipts", required=True, type=Path)
    backup.add_argument("--hermes-receipts", required=True, type=Path)
    backup.add_argument("--bundle", required=True, type=Path)
    _add_limits(backup)
    restore = commands.add_parser("verify-restore", help="Verify a canonical bundle into a new private target.")
    restore.add_argument("--bundle", required=True, type=Path)
    restore.add_argument("--target", required=True, type=Path)
    _add_limits(restore)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        limits = BackupLimits(max_files=args.max_files, max_bytes=args.max_bytes)
        command = _Command(args.command)
        match command:
            case _Command.BACKUP:
                result = create_backup(
                    BackupRequest(
                        cycle_database=args.cycle_db,
                        hermes_database=args.hermes_db,
                        cycle_receipts=args.cycle_receipts,
                        hermes_receipts=args.hermes_receipts,
                        destination=args.bundle,
                        limits=limits,
                    )
                )
            case _Command.VERIFY_RESTORE:
                result = verify_restore(RestoreRequest(args.bundle, args.target, limits))
            case unreachable:
                assert_never(unreachable)
    except BackupError as error:
        print(
            json.dumps(
                {
                    "broker_mutation": 0,
                    "heavy_processes": 0,
                    "model_calls": 0,
                    "provider_calls": 0,
                    "reason": error.reason.value,
                    "status": "invalid",
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(_summary(result, command), separators=(",", ":"), sort_keys=True))
    return 0


def _add_limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-files", required=True, type=int)
    parser.add_argument("--max-bytes", required=True, type=int)


def _summary(result: BackupResult, command: _Command) -> _Summary:
    return _Summary(
        artifact_count=result.artifact_count,
        broker_mutation=result.broker_mutation,
        heavy_processes=result.heavy_processes,
        manifest_sha256=result.manifest_sha256,
        model_calls=result.model_calls,
        operation=command.value,
        provider_calls=result.provider_calls,
        status="ok",
        total_bytes=result.total_bytes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
