#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run run_launchd_one_shot.py --help
# 3. Or make executable and run:
#      chmod +x run_launchd_one_shot.py
#      ./run_launchd_one_shot.py --help
# ──────────────────

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.launchd_one_shot import (
    OneShotInstallError,
    OneShotRequest,
    prepare_one_shot,
    submit_one_shot,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="macOS launchd에 at-most-once 실시간 검증 작업을 예약합니다."
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--run-at", required=True)
    parser.add_argument("--expires-at")
    parser.add_argument("--wrapper", type=Path, required=True)
    parser.add_argument("--stdout-log", type=Path, required=True)
    parser.add_argument("--stderr-log", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--persistent-plist", type=Path)
    parser.add_argument("--authority-repository", type=Path)
    parser.add_argument("--recovery-safe", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        request = OneShotRequest(
            label=args.label,
            run_at=args.run_at,
            wrapper=args.wrapper,
            stdout_log=args.stdout_log,
            stderr_log=args.stderr_log,
            receipt=args.receipt,
            command=tuple(command),
            expires_at=args.expires_at,
            persistent_plist=args.persistent_plist,
            authority_repository=args.authority_repository,
            recovery_safe=args.recovery_safe,
        )
        prepare_one_shot(request)
        if not args.prepare_only:
            submit_one_shot(request)
    except ValidationError:
        print(
            json.dumps({"reason": "invalid_request", "result": "blocked"}),
            file=sys.stderr,
        )
        return 2
    except OneShotInstallError as error:
        print(
            json.dumps({"reason": error.reason, "result": "blocked"}),
            file=sys.stderr,
        )
        return 1
    except OSError:
        print(
            json.dumps({"reason": "artifact_io_failed", "result": "blocked"}),
            file=sys.stderr,
        )
        return 1
    status = "prepared" if args.prepare_only else "scheduled"
    print(json.dumps({"label": request.label, "result": status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
