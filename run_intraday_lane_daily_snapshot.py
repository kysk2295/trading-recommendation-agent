#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx2

from trading_agent.alpaca_paper_config import load_alpaca_paper_credentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.intraday_lane_daily_snapshot import (
    finalize_intraday_lane_day,
    preflight_intraday_lane_day,
)
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.paper_runtime import CredentialLoader, PaperRuntimeReadiness
from trading_agent.paper_runtime_session import (
    PaperRuntimeProbeLoader,
    probe_paper_runtime,
)

REPORT_NAME = "intraday_lane_daily_snapshot_ko.md"
BLOCKED_REASON = "장종료·평탄·immutable 계보 조건을 확인하지 못했습니다"


@dataclass(frozen=True, slots=True)
class IntradayLaneDailySnapshotReport:
    result: str
    session_date: dt.date
    snapshot_state: str
    open_order_count: int | None
    open_position_count: int | None
    data_quality_complete: bool | None


def _session_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("session date는 YYYY-MM-DD 형식이어야 합니다") from error


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alpaca Paper GET/WSS 근거로 intraday lane 일일 snapshot을 확정")
    parser.add_argument("session", type=Path)
    parser.add_argument("--session-date", type=_session_date, required=True)
    parser.add_argument("--execution-database", type=Path, required=True)
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_loader: CredentialLoader = load_alpaca_paper_credentials,
    probe_loader: PaperRuntimeProbeLoader = probe_paper_runtime,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    registry = LaneRegistryStore(args.lane_registry)
    execution = ExecutionStore(args.execution_database)
    readiness: PaperRuntimeReadiness | None = None
    try:
        _ = preflight_intraday_lane_day(
            registry,
            execution,
            args.session,
            args.session_date,
            evaluated_at=clock(),
        )
        credentials = credential_loader()
        readiness = probe_loader(credentials, execution)
        result = finalize_intraday_lane_day(
            registry,
            execution,
            args.session,
            args.session_date,
            readiness,
            evaluated_at=clock(),
        )
    except (
        httpx2.HTTPError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
        sqlite3.Error,
    ):
        report = _blocked_report(args.session_date, readiness)
        return 1 if _write_report(args.output_dir, report) else 2

    snapshot = result.snapshot
    report = IntradayLaneDailySnapshotReport(
        result="finalized",
        session_date=snapshot.session_date,
        snapshot_state="created" if result.created else "replayed",
        open_order_count=snapshot.open_order_count,
        open_position_count=snapshot.open_position_count,
        data_quality_complete=snapshot.data_quality_complete,
    )
    return 0 if _write_report(args.output_dir, report) else 2


def _blocked_report(
    session_date: dt.date,
    readiness: PaperRuntimeReadiness | None,
) -> IntradayLaneDailySnapshotReport:
    if readiness is None:
        open_order_count = None
        open_position_count = None
    else:
        state = readiness.broker_state
        open_order_count = len(state.open_orders) + len(state.protective_ocos)
        open_position_count = sum(position.quantity != 0 for position in state.positions)
    return IntradayLaneDailySnapshotReport(
        result="blocked",
        session_date=session_date,
        snapshot_state="not_written",
        open_order_count=open_order_count,
        open_position_count=open_position_count,
        data_quality_complete=None,
    )


def _write_report(
    output_dir: Path,
    report: IntradayLaneDailySnapshotReport,
) -> bool:
    open_orders = "미평가" if report.open_order_count is None else str(report.open_order_count)
    open_positions = "미평가" if report.open_position_count is None else str(report.open_position_count)
    data_quality = (
        "미평가" if report.data_quality_complete is None else "예" if report.data_quality_complete else "아니오"
    )
    lines = [
        "# Intraday lane 일일 snapshot",
        "",
        "> 확정 수익이나 주문 승인이 아닌 Alpaca Paper 전진검증 확정 기록입니다.",
        "",
        f"- 결과: {report.result}",
        "- lane: intraday_momentum",
        f"- 거래일: {report.session_date.isoformat()}",
        f"- snapshot append: {report.snapshot_state}",
        f"- 미체결 주문: {open_orders}",
        f"- 열린 포지션: {open_positions}",
        f"- 데이터 품질 완료: {data_quality}",
        "- champion strategy: 없음",
        "- allocation eligible: 아니오",
        "- 자동 상태 변경: 금지",
        "- 외부 Alpaca mutation: 0건",
    ]
    if report.result == "blocked":
        lines.append(f"- 차단 사유: {BLOCKED_REASON}")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / REPORT_NAME
        temporary = destination.with_suffix(".tmp")
        _ = temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(destination)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
