#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.execution_errors import (
    ExecutionSchemaIntegrityError,
    UnsupportedExecutionSchemaError,
)
from trading_agent.lane_bootstrap import (
    InvalidLaneBootstrapError,
    bootstrap_lane_control_plane,
)
from trading_agent.lane_contract_models import InvalidLaneContractError
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryConflictError,
    LaneRegistryStore,
    LaneRegistryWriterLeaseUnavailableError,
    UnsupportedLaneRegistrySchemaError,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "lane_control_plane_bootstrap_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="로컬 append-only lane control-plane registry를 초기화")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--intraday-execution-database", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = bootstrap_lane_control_plane(
            LaneRegistryStore(args.database),
            args.intraday_execution_database,
        )
    except (
        InvalidLaneBootstrapError,
        InvalidLaneContractError,
        InvalidLaneRegistrySourceError,
        LaneRegistryConflictError,
        LaneRegistryWriterLeaseUnavailableError,
        ExecutionSchemaIntegrityError,
        UnsupportedExecutionSchemaError,
        UnsupportedLaneRegistrySchemaError,
        ValidationError,
        sqlite3.Error,
        OSError,
    ):
        _write_report(
            args.output_dir,
            "blocked",
            (
                "lane registry 또는 execution 원장의 immutable 계약을 확인하지 못했습니다",
                "외부 Alpaca mutation: 0건",
            ),
        )
        return 1
    _write_report(
        args.output_dir,
        "ready",
        (
            f"manifest 신규/전체: {result.manifests_created}/{result.manifests_total}",
            f"experiment scope 신규/전체: {result.scopes_created}/{result.scopes_total}",
            f"intraday account binding: {result.intraday_account_binding.value}",
            "외부 Alpaca mutation: 0건",
        ),
    )
    return 0


def _write_report(
    output_dir: Path,
    result: str,
    details: tuple[str, ...],
) -> None:
    lines = [
        "# Lane control-plane bootstrap",
        "",
        "> 주문 실행이나 확정 수익 기록이 아닌 로컬 control-plane 계약 초기화 결과입니다.",
        "",
        f"- 결과: {result}",
        *(f"- {detail}" for detail in details),
        "",
    ]
    write_private_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
