#!/usr/bin/env -S uv run --python 3.12 --with httpx2[http2,brotli,zstd] --with pydantic --with websockets>=16,<17 python
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "websockets>=16,<17"]
# ///
# How to run:
# ./run_alpaca_paper_bootstrap.py --database outputs/paper_execution/paper_execution.sqlite3

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
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
from trading_agent.execution_store import (
    AccountBindingConflictError,
    ExecutionStore,
    WriterLeaseUnavailableError,
)
from trading_agent.paper_execution_models import PaperBrokerState
from trading_agent.paper_runtime import (
    CredentialLoader,
    PaperStateLoader,
    read_paper_broker_state,
)
from trading_agent.private_report import write_private_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpaca paper 계좌를 단일 Writer 실행 원장에 GET-only로 결합")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("outputs/paper_execution/paper_execution.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/paper_execution/bootstrap/latest"),
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
        with store.writer() as writer:
            credentials = credential_loader()
            broker_state = state_loader(credentials)
            reasons = _bootstrap_reasons(store, broker_state)
            if reasons:
                _write_report(args.output_dir, bound=False, reasons=reasons)
                return 1
            inserted = writer.bind_account(
                broker_state.account.account_fingerprint,
                broker_state.account.observed_at,
            )
    except (
        AccountBindingConflictError,
        AlpacaApiError,
        AlpacaPaperSecretEncodingError,
        AlpacaPaperSecretFileError,
        ExecutionSchemaIntegrityError,
        MissingAlpacaPaperCredentialsError,
        PaperOrderReadIncompleteError,
        UnsupportedExecutionSchemaError,
        WriterLeaseUnavailableError,
        httpx2.HTTPError,
        OSError,
        sqlite3.Error,
    ) as error:
        print(_safe_error_reason(error), file=sys.stderr)
        return 2
    _write_report(
        args.output_dir,
        bound=True,
        reasons=("새 계좌 결합 완료" if inserted else "기존 계좌 결합 확인 완료",),
    )
    return 0


def _safe_error_reason(error: BaseException) -> str:
    return f"안전 오류 유형: {type(error).__name__}"


def _bootstrap_reasons(
    store: ExecutionStore,
    state: PaperBrokerState,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if state.account.trading_blocked:
        reasons.append("Alpaca paper 계좌가 거래 차단 상태입니다")
    if state.account.status != "ACTIVE":
        reasons.append(f"Alpaca paper 계좌 상태가 ACTIVE가 아닙니다: {state.account.status}")
    if state.open_orders:
        reasons.append(f"기존 paper 미체결 주문이 있습니다: {len(state.open_orders)}")
    if any(position.quantity != 0 for position in state.positions):
        reasons.append("기존 paper 포지션이 있습니다")
    if store.account_fingerprint() is None and store.intents():
        reasons.append("미결합 실행 원장에 기존 intent가 있습니다")
    return tuple(sorted(reasons))


def _write_report(
    output_dir: Path,
    *,
    bound: bool,
    reasons: tuple[str, ...],
) -> None:
    lines = [
        "# Alpaca Paper 실행 원장 Bootstrap",
        "",
        f"- 결합: {'완료' if bound else '거부'}",
        "- 외부 동작: 계좌·주문·포지션 GET only",
        "- 사유:",
        *(f"  - {reason}" for reason in reasons),
    ]
    destination = output_dir / "paper_bootstrap_ko.md"
    write_private_report(destination, "\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
