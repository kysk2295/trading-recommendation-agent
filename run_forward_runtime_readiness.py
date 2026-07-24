#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path

from trading_agent.forward_runtime_readiness import evaluate_forward_runtime_readiness

REPORT_NAME = "forward_runtime_readiness_ko.md"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _commit(value: str) -> str:
    if _COMMIT.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("commit은 소문자 40자리 SHA여야 합니다")
    return value


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("session date는 YYYY-MM-DD여야 합니다") from error


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("값은 양수여야 합니다")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="예약된 strict forward 런타임과 원장의 실행 준비성을 읽기 전용 검증")
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--expected-head", type=_commit, required=True)
    parser.add_argument("--required-commit", type=_commit, action="append", required=True)
    parser.add_argument("--session-date", type=_date, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--execution-database", type=Path, required=True)
    parser.add_argument("--cycles", type=_positive, required=True)
    parser.add_argument("--interval-seconds", type=_positive, required=True)
    parser.add_argument("--kis-server-attempts", type=_positive, required=True)
    parser.add_argument("--eod-last-bar-semantic-attempts", type=_positive, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    readiness = evaluate_forward_runtime_readiness(
        runtime_dir=args.runtime_dir,
        expected_head=args.expected_head,
        required_commits=tuple(args.required_commit),
        session_date=args.session_date,
        experiment_ledger=args.experiment_ledger,
        lane_registry=args.lane_registry,
        execution_database=args.execution_database,
        cycles=args.cycles,
        interval_seconds=args.interval_seconds,
        kis_server_attempts=args.kis_server_attempts,
        eod_last_bar_semantic_attempts=args.eod_last_bar_semantic_attempts,
    )
    _write_report(args.output_dir, blockers=readiness.blockers)
    return 0 if readiness.ready else 1


def _write_report(output_dir: Path, *, blockers: tuple[str, ...]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / REPORT_NAME
    lines = (
        "# Forward runtime readiness",
        "",
        "> 경로·자격증명·계좌 식별자를 기록하지 않는 읽기 전용 예약 실행 사전검증입니다.",
        "",
        f"- 결과: {'ready' if not blockers else 'blocked'}",
        f"- 필수 계약 확인: {'완료' if not blockers else '미완료'}",
        f"- blocker 수: {len(blockers)}",
        *(f"- blocker: {blocker}" for blocker in blockers),
        "- 외부 broker mutation: 0건",
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
        temporary_path.chmod(0o600)
        temporary_path.replace(target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
