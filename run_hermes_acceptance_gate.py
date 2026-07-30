from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.hermes_acceptance_evidence import (
    HermesAcceptanceBuildRequest,
    InvalidHermesAcceptanceBuildError,
    build_hermes_acceptance_evidence,
    verify_hermes_acceptance_evidence,
)
from trading_agent.hermes_acceptance_gate import (
    HermesAcceptanceGateStatus,
    current_hermes_acceptance_waiting,
)
from trading_agent.hermes_delivery_errors import InvalidHermesDeliveryStoreError
from trading_agent.hermes_delivery_reader import HermesDeliveryReader


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail-closed Hermes aggregate acceptance gate")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="report current aggregate evidence availability")
    status.add_argument("--database", type=Path, required=True)
    build = commands.add_parser("build", help="build an AC-001 aggregate report and manifest")
    build.add_argument("--request", type=Path, required=True)
    build.add_argument("--repository", type=Path, default=Path.cwd())
    build.add_argument("--report", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)
    verify = commands.add_parser("verify", help="verify an AC-001 aggregate report and manifest")
    verify.add_argument("--repository", type=Path, default=Path.cwd())
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        match args.command:
            case "status":
                if not args.database.is_file():
                    raise InvalidHermesDeliveryStoreError
                _ = HermesDeliveryReader(args.database).events()
                result = current_hermes_acceptance_waiting()
                _print(
                    result.status,
                    result.reasons[0].value,
                    result.us_real_session_count,
                    result.kr_real_session_count,
                )
                return 0
            case "build":
                request = HermesAcceptanceBuildRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
                result = build_hermes_acceptance_evidence(request, args.repository, args.report, args.manifest)
                _print(
                    result.report.assessment.status,
                    result.report.assessment.reasons[0].value if result.report.assessment.reasons else None,
                    result.report.assessment.us_real_session_count,
                    result.report.assessment.kr_real_session_count,
                )
                return 0 if result.manifest is not None else 1
            case "verify":
                report = verify_hermes_acceptance_evidence(args.report, args.manifest, args.repository)
                _print(
                    report.assessment.status,
                    None,
                    report.assessment.us_real_session_count,
                    report.assessment.kr_real_session_count,
                )
                return 0
            case unreachable:
                assert_never(unreachable)
    except (
        InvalidHermesAcceptanceBuildError,
        InvalidHermesDeliveryStoreError,
        OSError,
        sqlite3.DatabaseError,
        UnicodeError,
        ValidationError,
    ):
        _print(HermesAcceptanceGateStatus.BLOCKED, "invalid_acceptance_evidence", 0, 0)
        return 2


def _print(status: HermesAcceptanceGateStatus, reason: str | None, us_count: int, kr_count: int) -> None:
    payload = {
        "kr_real_session_count": kr_count,
        "result": status.value,
        "us_real_session_count": us_count,
    }
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
