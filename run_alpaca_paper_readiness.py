#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx2

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_client import PaperOrderReadIncompleteError
from trading_agent.alpaca_paper_config import (
    AlpacaPaperSecretEncodingError,
    AlpacaPaperSecretFileError,
    MissingAlpacaPaperCredentialsError,
    load_alpaca_paper_credentials,
)
from trading_agent.alpaca_paper_order_stream import PaperOrderStreamError
from trading_agent.execution_errors import (
    ExecutionSchemaIntegrityError,
    UnsupportedExecutionSchemaError,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_runtime import CredentialLoader, PaperRuntimeEpochChangedError
from trading_agent.paper_runtime_session import (
    PaperRuntimeProbeLoader,
    probe_paper_runtime,
)


@dataclass(frozen=True, slots=True)
class RuntimeReadinessReport:
    checked_at: dt.datetime | None
    stream_ready: bool
    reconciliation_ready: bool
    market_open: bool
    open_order_count: int
    position_count: int
    reasons: tuple[str, ...]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpaca paper 주문 스트림과 REST 상태를 읽기 전용으로 대사")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("outputs/paper_execution/paper_execution.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/paper_execution/readiness/latest"),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_loader: CredentialLoader = load_alpaca_paper_credentials,
    probe_loader: PaperRuntimeProbeLoader = probe_paper_runtime,
) -> int:
    args = _parser().parse_args(argv)
    store = ExecutionStore(args.database)
    try:
        if not store.is_initialized():
            _write_report(
                args.output_dir,
                RuntimeReadinessReport(
                    checked_at=None,
                    stream_ready=False,
                    reconciliation_ready=False,
                    market_open=False,
                    open_order_count=0,
                    position_count=0,
                    reasons=("실행 원장이 초기화되지 않았습니다",),
                ),
            )
            return 1
        credentials = credential_loader()
        readiness = probe_loader(credentials, store)
        state = readiness.broker_state
    except (
        AlpacaApiError,
        AlpacaPaperSecretEncodingError,
        AlpacaPaperSecretFileError,
        ExecutionSchemaIntegrityError,
        MissingAlpacaPaperCredentialsError,
        PaperOrderReadIncompleteError,
        PaperOrderStreamError,
        PaperRuntimeEpochChangedError,
        UnsupportedExecutionSchemaError,
        httpx2.HTTPError,
        OSError,
        sqlite3.Error,
    ) as error:
        rendered = _safe_error_reason(error)
        print(rendered, file=sys.stderr)
        _write_report(
            args.output_dir,
            RuntimeReadinessReport(
                checked_at=None,
                stream_ready=False,
                reconciliation_ready=False,
                market_open=False,
                open_order_count=0,
                position_count=0,
                reasons=(rendered,),
            ),
        )
        return 2

    report = RuntimeReadinessReport(
        checked_at=readiness.stream_heartbeat.pong_at,
        stream_ready=True,
        reconciliation_ready=readiness.ready,
        market_open=readiness.market_clock.is_open,
        open_order_count=len(state.open_orders),
        position_count=len(state.positions),
        reasons=readiness.reasons,
    )
    _write_report(args.output_dir, report)
    return 0 if readiness.ready else 1


def _safe_error_reason(error: BaseException) -> str:
    return f"안전 오류 유형: {type(error).__name__}"


def _write_report(output_dir: Path, report: RuntimeReadinessReport) -> None:
    reasons = report.reasons or ("없음",)
    lines = [
        "# Alpaca Paper 런타임 준비상태",
        "",
        (f"- 확인 시각: {report.checked_at.isoformat()}" if report.checked_at is not None else "- 확인 시각: 없음"),
        "- endpoint: paper-api.alpaca.markets 고정",
        ("- 주문 스트림: 인증·구독·Pong 확인" if report.stream_ready else "- 주문 스트림: 차단"),
        (
            "- 활성 스트림 내부 REST·원장·포트폴리오 대사: 통과"
            if report.reconciliation_ready
            else "- 활성 스트림 내부 REST·원장·포트폴리오 대사: 차단"
        ),
        f"- 브로커 시장 개장: {'예' if report.market_open else '아니오'}",
        f"- 미체결 주문: {report.open_order_count}",
        f"- 열린 포지션: {report.position_count}",
        "- 신규 주문 승인: 미평가 (current-bar와 후보 주문 입력 필요)",
        "- 주문 POST/DELETE: 비활성",
        "- 사유:",
        *(f"  - {reason}" for reason in reasons),
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "paper_runtime_readiness_ko.md"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(destination)


if __name__ == "__main__":
    raise SystemExit(main())
