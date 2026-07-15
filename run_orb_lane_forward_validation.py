#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from trading_agent.scan_cycle import append_cycle_audit

REPORT_NAME = "orb_lane_forward_validation_ko.md"
SNAPSHOT_AUDIT_NAME = "post_session_intraday_snapshot_cycles.csv"
REVIEW_AUDIT_NAME = "post_session_lane_reviewer_cycles.csv"

CommandRunner = Callable[[tuple[str, ...]], int]
Clock = Callable[[], dt.datetime]


@dataclass(frozen=True, slots=True)
class LaneForwardValidationPaths:
    session: Path
    execution_database: Path
    lane_registry: Path
    review_ledger: Path
    output_dir: Path


@dataclass(frozen=True, slots=True)
class LaneForwardValidationResult:
    snapshot_exit_code: int
    reviewer_exit_code: int | None

    @property
    def completed(self) -> bool:
        return self.snapshot_exit_code == 0 and self.reviewer_exit_code == 0


def snapshot_command(
    paths: LaneForwardValidationPaths,
    session_date: dt.date,
) -> tuple[str, ...]:
    date_text = session_date.isoformat()
    return (
        str(Path(__file__).with_name("run_intraday_lane_daily_snapshot.py")),
        str(paths.session),
        "--session-date",
        date_text,
        "--execution-database",
        str(paths.execution_database),
        "--lane-registry",
        str(paths.lane_registry),
        "--output-dir",
        str(paths.output_dir / "snapshots" / date_text),
    )


def reviewer_command(
    paths: LaneForwardValidationPaths,
    session_date: dt.date,
) -> tuple[str, ...]:
    date_text = session_date.isoformat()
    return (
        str(Path(__file__).with_name("run_lane_reviewer.py")),
        str(paths.session),
        "--session-date",
        date_text,
        "--lane-registry",
        str(paths.lane_registry),
        "--review-ledger",
        str(paths.review_ledger),
        "--output-dir",
        str(paths.output_dir / "reviews" / date_text),
    )


def run_forward_validation(
    paths: LaneForwardValidationPaths,
    session_date: dt.date,
    *,
    runner: CommandRunner = lambda command: subprocess.run(command, check=False).returncode,
    clock: Clock = lambda: dt.datetime.now().astimezone(),
) -> LaneForwardValidationResult:
    snapshot_exit_code = _run_phase(
        snapshot_command(paths, session_date),
        paths.output_dir / SNAPSHOT_AUDIT_NAME,
        runner,
        clock,
    )
    if snapshot_exit_code != 0:
        return LaneForwardValidationResult(snapshot_exit_code, None)
    reviewer_exit_code = _run_phase(
        reviewer_command(paths, session_date),
        paths.output_dir / REVIEW_AUDIT_NAME,
        runner,
        clock,
    )
    return LaneForwardValidationResult(snapshot_exit_code, reviewer_exit_code)


def _run_phase(
    command: tuple[str, ...],
    audit_path: Path,
    runner: CommandRunner,
    clock: Clock,
) -> int:
    started_at = clock()
    try:
        exit_code = runner(command)
    except OSError:
        exit_code = 1
    try:
        append_cycle_audit(audit_path, started_at, exit_code)
    except OSError:
        return 1
    return exit_code


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("session date는 YYYY-MM-DD 형식이어야 합니다") from error


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ORB intraday snapshot과 독립 Reviewer를 장후 순차 실행")
    parser.add_argument("session", type=Path)
    parser.add_argument("--session-date", type=_session_date, required=True)
    parser.add_argument("--execution-database", type=Path, required=True)
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--review-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CommandRunner = lambda command: subprocess.run(command, check=False).returncode,
    clock: Clock = lambda: dt.datetime.now().astimezone(),
) -> int:
    args = parse_args(argv)
    paths = LaneForwardValidationPaths(
        session=args.session,
        execution_database=args.execution_database,
        lane_registry=args.lane_registry,
        review_ledger=args.review_ledger,
        output_dir=args.output_dir,
    )
    result = run_forward_validation(
        paths,
        args.session_date,
        runner=runner,
        clock=clock,
    )
    if not _write_report(paths.output_dir, args.session_date, result):
        return 2
    return 0 if result.completed else 1


def _write_report(
    output_dir: Path,
    session_date: dt.date,
    result: LaneForwardValidationResult,
) -> bool:
    lines = [
        "# ORB lane daily forward validation",
        "",
        "> 확정 수익, champion 선언 또는 주문 승인이 아닌 Paper forward-validation 기록입니다.",
        "",
        f"- 결과: {'completed' if result.completed else 'blocked'}",
        "- lane: intraday_momentum",
        f"- 거래일: {session_date.isoformat()}",
        f"- snapshot phase: {_phase_status(result.snapshot_exit_code)}",
        f"- Reviewer phase: {_phase_status(result.reviewer_exit_code)}",
        "- 자동 상태 변경: 금지",
        "- 주문 권한 변경: 금지",
        "- champion 선언: 없음",
        "- Portfolio Manager 배분: 없음",
        "- 외부 Alpaca mutation: 0건",
    ]
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / REPORT_NAME
        temporary = destination.with_suffix(".tmp")
        _ = temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(destination)
    except OSError:
        return False
    return True


def _phase_status(exit_code: int | None) -> str:
    if exit_code is None:
        return "not_started"
    return "success" if exit_code == 0 else "failed"


if __name__ == "__main__":
    raise SystemExit(main())
