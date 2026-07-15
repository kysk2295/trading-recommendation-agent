#!/usr/bin/env -S uv run --python 3.12 --with httpx2[http2,brotli,zstd] --with pydantic python
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///
# How to run:
# ./run_alpaca_paper_preflight.py --database outputs/paper_execution/paper_execution.sqlite3

from __future__ import annotations

import argparse
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
from trading_agent.execution_errors import (
    ExecutionSchemaIntegrityError,
    UnsupportedExecutionSchemaError,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_reconciliation import (
    PaperReconciliationSnapshot,
    reconcile_paper_state,
)
from trading_agent.paper_runtime import (
    CredentialLoader,
    PaperStateLoader,
    read_paper_broker_state,
)
from trading_agent.private_report import write_private_report


@dataclass(frozen=True, slots=True)
class PreflightReport:
    ready: bool
    open_order_count: int
    position_count: int
    reasons: tuple[str, ...]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpaca paper 계좌와 로컬 실행 원장의 GET-only 안전 대사")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("outputs/paper_execution/paper_execution.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/paper_execution/preflight/latest"),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_loader: CredentialLoader = load_alpaca_paper_credentials,
    state_loader: PaperStateLoader = read_paper_broker_state,
) -> int:
    args = _parser().parse_args(argv)
    store = ExecutionStore(args.database)
    try:
        if not store.is_initialized():
            _write_report(
                args.output_dir,
                PreflightReport(
                    ready=False,
                    open_order_count=0,
                    position_count=0,
                    reasons=("실행 원장이 초기화되지 않았습니다",),
                ),
            )
            return 1
        credentials = credential_loader()
        broker_state = state_loader(credentials)
        ledger = store.reconciliation_ledger()
        result = reconcile_paper_state(
            PaperReconciliationSnapshot(
                account=broker_state.account,
                broker_orders=broker_state.open_orders,
                positions=broker_state.positions,
                stored_intents=ledger.intents,
                unresolved_intent_ids=ledger.unresolved_intent_ids,
                bound_account_fingerprint=ledger.account_fingerprint,
                order_states=ledger.order_states,
            )
        )
    except (
        AlpacaApiError,
        AlpacaPaperSecretEncodingError,
        AlpacaPaperSecretFileError,
        ExecutionSchemaIntegrityError,
        MissingAlpacaPaperCredentialsError,
        PaperOrderReadIncompleteError,
        UnsupportedExecutionSchemaError,
        httpx2.HTTPError,
        OSError,
        sqlite3.Error,
    ) as error:
        print(_safe_error_reason(error), file=sys.stderr)
        return 2
    _write_report(
        args.output_dir,
        PreflightReport(
            ready=result.ready,
            open_order_count=len(broker_state.open_orders),
            position_count=len(broker_state.positions),
            reasons=result.reasons,
        ),
    )
    return 0 if result.ready else 1


def _safe_error_reason(error: BaseException) -> str:
    return f"안전 오류 유형: {type(error).__name__}"


def _write_report(
    output_dir: Path,
    report: PreflightReport,
) -> None:
    lines = [
        "# Alpaca Paper 안전 대사",
        "",
        "- 계좌 별칭: alpaca-paper",
        f"- 준비: {'예' if report.ready else '아니오'}",
        f"- 미체결 주문: {report.open_order_count}",
        f"- 열린 포지션: {report.position_count}",
        "- 사유:",
        *(f"  - {reason}" for reason in report.reasons),
    ]
    destination = output_dir / "paper_preflight_ko.md"
    write_private_report(destination, "\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
