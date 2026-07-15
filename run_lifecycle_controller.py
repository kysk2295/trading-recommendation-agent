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
import tempfile
from collections.abc import Sequence
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
from trading_agent.lifecycle_controller import (
    InvalidLifecycleControllerSourceError,
    control_intraday_orb_lifecycle,
)

REPORT_NAME = "lifecycle_controller_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="로컬 immutable evidence로 ORB lifecycle next-session 결정을 평가")
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--review-ledger", type=Path, required=True)
    parser.add_argument("--session-date", type=_session_date, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    decided_at: dt.datetime | None = None,
) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(dt.UTC) if decided_at is None else decided_at
    try:
        result = control_intraday_orb_lifecycle(
            lane_registry=LaneRegistryReader(args.lane_registry),
            review_ledger=LaneReviewReader(args.review_ledger),
            experiment_ledger=ExperimentLedgerStore(args.experiment_ledger),
            session_date=args.session_date,
            decided_at=timestamp,
        )
    except (
        InvalidLifecycleControllerSourceError,
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
        OSError,
    ):
        _write_report(
            args.output_dir,
            (
                "result: blocked_source",
                "external broker mutation: 0",
            ),
        )
        return 1

    _write_report(
        args.output_dir,
        (
            "result: completed",
            f"outcome: {result.outcome.value}",
            f"created: {str(result.created).lower()}",
            f"from_state: {result.from_state.value}",
            f"to_state: {'none' if result.to_state is None else result.to_state.value}",
            f"policy_blockers: {','.join(result.blockers) if result.blockers else 'none'}",
            "external broker mutation: 0",
        ),
    )
    return 0


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("session date는 YYYY-MM-DD 형식이어야 합니다") from error


def _write_report(output_dir: Path, details: tuple[str, ...]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / REPORT_NAME
    mode = target.stat().st_mode & 0o777 if target.is_file() else 0o600
    lines = (
        "# Lifecycle Controller",
        "",
        "> 주문·champion·위험예산 권한이 없는 local evidence 평가 결과입니다.",
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
