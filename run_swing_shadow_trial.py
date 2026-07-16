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
    ExperimentLedgerReader,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.swing_shadow_review_store import (
    InvalidSwingShadowReviewSourceError,
    SwingShadowReviewConflictError,
    SwingShadowReviewStore,
    SwingShadowReviewWriterLeaseUnavailableError,
)
from trading_agent.swing_shadow_reviewer import (
    InvalidSwingShadowReviewError,
    review_swing_shadow_trial,
)
from trading_agent.swing_shadow_store import (
    InvalidSwingShadowLedgerError,
    SwingShadowConflictError,
    SwingShadowReader,
    SwingShadowWriterLeaseUnavailableError,
)
from trading_agent.swing_shadow_trial import (
    InvalidSwingShadowTrialSourceError,
    finalize_swing_shadow_trial,
    register_swing_shadow_trial,
    start_swing_shadow_trial,
)

REPORT_NAME = "swing_shadow_trial_ko.md"


@dataclass(frozen=True, slots=True)
class _CliOutcome:
    created: bool
    outcome_kind: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="US swing shadow trial을 local immutable evidence로 운영")
    operations = parser.add_subparsers(dest="operation", required=True)
    for operation, help_text in (
        ("register", "다음 정규장 개장 전 signal trial 사전등록"),
        ("start", "정규장 안에서 preregistered trial 시작"),
        ("finalize", "관찰된 shadow terminal로 completed 확정"),
    ):
        command = operations.add_parser(operation, help=help_text)
        _add_common_arguments(command)
    review = operations.add_parser("review", help="완료된 trial을 authority 없이 독립 검토")
    _add_common_arguments(review)
    review.add_argument("--review-ledger", type=Path, required=True)
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
        outcome = _execute(args, timestamp, runtime_code_version=runtime_code_version)
    except _CLI_ERRORS:
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
            f"{_outcome_label(args.operation)}: {outcome.outcome_kind}",
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
    shadow = SwingShadowReader(args.shadow_ledger)
    if args.operation == "register":
        code_version = _current_code_version() if runtime_code_version is None else runtime_code_version
        result = register_swing_shadow_trial(
            experiment_ledger=ExperimentLedgerStore(args.experiment_ledger),
            shadow_ledger=shadow,
            signal_id=args.signal_id,
            runtime_code_version=code_version,
            registered_at=timestamp,
        )
        return _CliOutcome(result.created, "none")
    if args.operation == "start":
        result = start_swing_shadow_trial(
            experiment_ledger=ExperimentLedgerStore(args.experiment_ledger),
            shadow_ledger=shadow,
            signal_id=args.signal_id,
            started_at=timestamp,
        )
        return _CliOutcome(result.created, result.event.event_kind.value)
    if args.operation == "finalize":
        result = finalize_swing_shadow_trial(
            experiment_ledger=ExperimentLedgerStore(args.experiment_ledger),
            shadow_ledger=shadow,
            signal_id=args.signal_id,
            finalized_at=timestamp,
        )
        return _CliOutcome(result.created, result.event.event_kind.value)
    if args.operation == "review":
        result = review_swing_shadow_trial(
            experiment_ledger=ExperimentLedgerReader(args.experiment_ledger),
            shadow_ledger=shadow,
            reviews=SwingShadowReviewStore(args.review_ledger),
            signal_id=args.signal_id,
            reviewed_at=timestamp,
        )
        return _CliOutcome(result.created, result.event.reviewer_action.value)
    raise ValueError("unsupported operation")


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--shadow-ledger", type=Path, required=True)
    parser.add_argument("--signal-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)


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
    return revision + (".dirty" if dirty else "")


def _outcome_label(operation: str) -> str:
    return "reviewer_action" if operation == "review" else "event_kind"


def _write_report(output_dir: Path, details: tuple[str, ...]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / REPORT_NAME
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
            _ = handle.write(
                "\n".join(
                    (
                        "# US swing shadow trial",
                        "",
                        "> 주문, champion, 배분 권한이 없는 local forward-validation 결과입니다.",
                        "",
                        *(f"- {detail}" for detail in details),
                        "",
                    )
                )
            )
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(0o600)
        temporary_path.replace(target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


_CLI_ERRORS = (
    ExperimentLedgerConflictError,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
    InvalidSwingShadowLedgerError,
    SwingShadowConflictError,
    SwingShadowWriterLeaseUnavailableError,
    InvalidSwingShadowTrialSourceError,
    InvalidSwingShadowReviewError,
    InvalidSwingShadowReviewSourceError,
    SwingShadowReviewConflictError,
    SwingShadowReviewWriterLeaseUnavailableError,
    ValidationError,
    sqlite3.Error,
    subprocess.SubprocessError,
    OSError,
    UnicodeError,
    ValueError,
)


if __name__ == "__main__":
    raise SystemExit(main())
