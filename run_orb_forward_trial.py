#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)
from trading_agent.lane_review_store import (
    InvalidLaneReviewSourceError,
    LaneReviewReader,
    UnsupportedLaneReviewSchemaError,
)
from trading_agent.orb_forward_trial import (
    InvalidOrbForwardTrialSourceError,
    OrbTrialFailurePhase,
    fail_orb_shadow_trial,
    finalize_orb_shadow_trial,
    register_orb_shadow_trial,
    start_orb_shadow_trial,
)

REPORT_NAME = "orb_forward_trial_ko.md"


@dataclass(frozen=True, slots=True)
class _CliOutcome:
    created: bool
    event_kind: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ORB 일일 shadow trial을 로컬 immutable evidence로 운영")
    operations = parser.add_subparsers(dest="operation", required=True)

    register = operations.add_parser("register", help="NYSE open 전 일일 trial 사전등록")
    _add_experiment_source(register)
    register.add_argument("--lane-registry", type=Path, required=True)
    _add_session_and_output(register)

    start = operations.add_parser("start", help="정규장 안에서 preregistered trial 시작")
    _add_experiment_source(start)
    _add_session_and_output(start)

    finalize = operations.add_parser("finalize", help="장후 exact evidence를 completed/censored로 확정")
    finalize.add_argument("session", type=Path)
    _add_experiment_source(finalize)
    finalize.add_argument("--lane-registry", type=Path, required=True)
    finalize.add_argument("--review-ledger", type=Path, required=True)
    _add_session_and_output(finalize)

    fail = operations.add_parser("fail", help="검증된 nonzero phase audit로 failed 확정")
    _add_experiment_source(fail)
    fail.add_argument(
        "--phase",
        type=OrbTrialFailurePhase,
        choices=tuple(OrbTrialFailurePhase),
        required=True,
    )
    fail.add_argument("--audit", type=Path, required=True)
    _add_session_and_output(fail)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    now: dt.datetime | None = None,
    runtime_code_version: str | None = None,
) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(dt.UTC) if now is None else now
    try:
        outcome = _execute(
            args,
            timestamp,
            runtime_code_version=runtime_code_version,
        )
    except (
        InvalidOrbForwardTrialSourceError,
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        UnsupportedExperimentLedgerSchemaError,
        InvalidLaneRegistrySourceError,
        UnsupportedLaneRegistrySchemaError,
        InvalidLaneReviewSourceError,
        UnsupportedLaneReviewSchemaError,
        ValidationError,
        sqlite3.Error,
        subprocess.SubprocessError,
        OSError,
        UnicodeError,
        ValueError,
    ):
        _write_report(
            args.output_dir,
            (
                "result: blocked_source",
                f"operation: {args.operation}",
                "external broker mutation: 0",
            ),
        )
        return 1

    _write_report(
        args.output_dir,
        (
            "result: completed",
            f"operation: {args.operation}",
            f"created: {str(outcome.created).lower()}",
            f"event_kind: {outcome.event_kind}",
            "external broker mutation: 0",
        ),
    )
    return 0


def _execute(
    args: argparse.Namespace,
    timestamp: dt.datetime,
    *,
    runtime_code_version: str | None,
) -> _CliOutcome:
    experiment_ledger = ExperimentLedgerStore(args.experiment_ledger)
    if args.operation == "register":
        code_version = _current_code_version() if runtime_code_version is None else runtime_code_version
        result = register_orb_shadow_trial(
            lane_registry=LaneRegistryReader(args.lane_registry),
            experiment_ledger=experiment_ledger,
            session_date=args.session_date,
            runtime_code_version=code_version,
            registered_at=timestamp,
        )
        return _CliOutcome(result.created, "none")
    if args.operation == "start":
        result = start_orb_shadow_trial(
            experiment_ledger=experiment_ledger,
            session_date=args.session_date,
            started_at=timestamp,
        )
        return _CliOutcome(result.created, result.event.event_kind.value)
    if args.operation == "finalize":
        result = finalize_orb_shadow_trial(
            experiment_ledger=experiment_ledger,
            lane_registry=LaneRegistryReader(args.lane_registry),
            review_ledger=LaneReviewReader(args.review_ledger),
            session=args.session,
            session_date=args.session_date,
            occurred_at=timestamp,
        )
        return _CliOutcome(result.created, result.event.event_kind.value)
    if args.operation == "fail":
        result = fail_orb_shadow_trial(
            experiment_ledger=experiment_ledger,
            session_date=args.session_date,
            phase=args.phase,
            audit=args.audit,
            occurred_at=timestamp,
        )
        return _CliOutcome(result.created, result.event.event_kind.value)
    raise ValueError("unsupported operation")


def _add_experiment_source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--experiment-ledger", type=Path, required=True)


def _add_session_and_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-date", type=_session_date, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("session date는 YYYY-MM-DD 형식이어야 합니다") from error


def _current_code_version() -> str:
    project = Path(__file__).parent
    revision = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return revision + ("+dirty" if dirty else "")


def _write_report(output_dir: Path, details: tuple[str, ...]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / REPORT_NAME
    mode = 0o600
    lines = (
        "# ORB daily shadow trial",
        "",
        "> 주문·champion·위험예산 권한이 없는 local forward-validation 결과입니다.",
        "",
        *(f"- {detail}" for detail in details),
        "",
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_dir,
            prefix=f".{REPORT_NAME}.",
            suffix=".writing",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            _ = handle.write("\n".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(mode)
        temporary_path.replace(target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
