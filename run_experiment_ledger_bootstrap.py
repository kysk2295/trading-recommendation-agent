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

from trading_agent.experiment_ledger_bootstrap import (
    InvalidExperimentLedgerBootstrapSourceError,
    bootstrap_current_intraday_experiments,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.lane_registry_store import LaneRegistryReader

REPORT_NAME = "experiment_ledger_bootstrap_ko.md"
CURRENT_CONTRACT_COUNT = 4


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="검증된 intraday 연구 계약을 로컬 전역 experiment ledger에 등록")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--lane-registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--code-version", required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    recorded_at: dt.datetime | None = None,
) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(dt.UTC) if recorded_at is None else recorded_at
    try:
        result = bootstrap_current_intraday_experiments(
            lane_registry=LaneRegistryReader(args.lane_registry),
            experiment_ledger=ExperimentLedgerStore(args.database),
            code_version=args.code_version,
            recorded_at=timestamp,
        )
    except (
        InvalidExperimentLedgerBootstrapSourceError,
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        UnsupportedExperimentLedgerSchemaError,
        ValidationError,
        sqlite3.Error,
        OSError,
    ):
        _write_report(
            args.output_dir,
            result="blocked",
            details=(
                "immutable lane source 또는 experiment ledger 계약을 확인하지 못했습니다",
                "외부 broker mutation: 0건",
            ),
        )
        return 1

    _write_report(
        args.output_dir,
        result="ready",
        details=(
            _created_reused("hypothesis", result.hypotheses_created),
            _created_reused("strategy version", result.versions_created),
            _created_reused("strategy authority", result.authority_bindings_created),
            _created_reused("lifecycle event", result.lifecycle_events_created),
            "state: experimental_shadow",
            "외부 broker mutation: 0건",
        ),
    )
    return 0


def _created_reused(label: str, created: int) -> str:
    return f"{label} 신규/재사용: {created}/{CURRENT_CONTRACT_COUNT - created}"


def _write_report(
    output_dir: Path,
    *,
    result: str,
    details: tuple[str, ...],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / REPORT_NAME
    mode = target.stat().st_mode & 0o777 if target.is_file() else 0o600
    lines = (
        "# Global experiment ledger bootstrap",
        "",
        "> 주문·위험예산·전략 승격 권한이 없는 로컬 immutable 연구 계보 등록 결과입니다.",
        "",
        f"- 결과: {result}",
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
