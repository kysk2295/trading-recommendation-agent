#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx2

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_activities import PaperActivityHistoryIncompleteError
from trading_agent.alpaca_paper_client import PaperOrderReadIncompleteError
from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    AlpacaPaperSecretEncodingError,
    AlpacaPaperSecretFileError,
    MissingAlpacaPaperCredentialsError,
    load_alpaca_paper_credentials,
)
from trading_agent.alpaca_paper_order_stream import PaperOrderStreamError
from trading_agent.execution_errors import (
    AccountBindingConflictError,
    ExecutionSchemaIntegrityError,
    UnsupportedExecutionSchemaError,
)
from trading_agent.execution_store import ExecutionStore, WriterLeaseUnavailableError
from trading_agent.paper_account_activity_store import (
    InvalidPaperAccountActivityError,
    PaperAccountActivityConflictError,
)
from trading_agent.paper_mutation_recovery import (
    InvalidPaperMutationRecoverySnapshotError,
    PaperMutationRecoveryAccountError,
)
from trading_agent.paper_mutation_recovery_models import (
    PaperMutationRecoveryResult,
    PaperMutationRecoveryState,
)
from trading_agent.paper_operating_session_models import (
    PaperMutationRecoveryBarrierError,
)
from trading_agent.paper_protective_oco_recovery_store import (
    InvalidProtectiveOcoRecoveryError,
    ProtectiveOcoRecoveryConflictError,
)
from trading_agent.paper_runtime import PaperRuntimeEpochChangedError
from trading_agent.paper_stream_recovery import (
    InvalidPaperStreamRecoveryError,
    PaperStreamRecoveryConflictError,
)
from trading_agent.paper_stream_recovery_runtime import (
    PaperStreamRecoveryIncompleteError,
)
from trading_agent.paper_trade_update_runtime import (
    recover_current_paper_mutations,
)
from trading_agent.trade_update_receipts import (
    InvalidTradeUpdateRawReceiptError,
    TradeUpdateReceiptConflictError,
    UnknownTradeUpdateReceiptError,
)

type CredentialLoader = Callable[[], AlpacaPaperCredentials]
type RecoveryLoader = Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    tuple[PaperMutationRecoveryResult, ...],
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Alpaca Paper mutation timeout을 current-epoch WSS·REST GET으로만 복구",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("outputs/paper_execution/paper_execution.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/paper_execution/mutation_recovery/latest"),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_loader: CredentialLoader = load_alpaca_paper_credentials,
    recovery_loader: RecoveryLoader = recover_current_paper_mutations,
) -> int:
    args = _parser().parse_args(argv)
    store = ExecutionStore(args.database)
    if not store.path.is_file():
        _write_report(args.output_dir, (), ("실행 원장이 초기화되지 않았습니다",))
        return 1
    try:
        with store.writer():
            pass
        results = recovery_loader(credential_loader(), store)
    except (
        AccountBindingConflictError,
        AlpacaApiError,
        AlpacaPaperSecretEncodingError,
        AlpacaPaperSecretFileError,
        ExecutionSchemaIntegrityError,
        InvalidPaperAccountActivityError,
        InvalidPaperMutationRecoverySnapshotError,
        InvalidPaperStreamRecoveryError,
        InvalidProtectiveOcoRecoveryError,
        InvalidTradeUpdateRawReceiptError,
        MissingAlpacaPaperCredentialsError,
        PaperAccountActivityConflictError,
        PaperActivityHistoryIncompleteError,
        PaperMutationRecoveryAccountError,
        PaperMutationRecoveryBarrierError,
        PaperOrderReadIncompleteError,
        PaperOrderStreamError,
        PaperRuntimeEpochChangedError,
        PaperStreamRecoveryConflictError,
        PaperStreamRecoveryIncompleteError,
        ProtectiveOcoRecoveryConflictError,
        TradeUpdateReceiptConflictError,
        UnknownTradeUpdateReceiptError,
        UnsupportedExecutionSchemaError,
        WriterLeaseUnavailableError,
        httpx2.HTTPError,
        OSError,
        sqlite3.Error,
    ) as error:
        rendered = _safe_error_reason(error)
        print(rendered, file=sys.stderr)
        _write_report(args.output_dir, (), (rendered,))
        return 2
    _write_report(args.output_dir, results, ())
    return int(any(result.state is PaperMutationRecoveryState.UNRESOLVED for result in results))


def _safe_error_reason(error: BaseException) -> str:
    return f"안전 오류 유형: {type(error).__name__}"


def _write_report(
    output_dir: Path,
    results: tuple[PaperMutationRecoveryResult, ...],
    blockers: tuple[str, ...],
) -> None:
    acknowledged = sum(result.state is PaperMutationRecoveryState.ACKNOWLEDGED for result in results)
    absent = sum(result.state is PaperMutationRecoveryState.ABSENT for result in results)
    unresolved = sum(result.state is PaperMutationRecoveryState.UNRESOLVED for result in results)
    lines = (
        "# Alpaca Paper mutation current-epoch 복구",
        "",
        f"- 복구 대상: {len(results)}",
        f"- 확인 완료: {acknowledged}",
        f"- 부재 확인: {absent}",
        f"- 미해결: {unresolved}",
        "- 차단 사유:",
        *(f"  - {reason}" for reason in (blockers or ("없음",))),
        "- 외부 동작: Alpaca Paper WSS + REST GET only",
        "- 로컬 동작: schema v7 append-only recovery event 저장",
        "- 주문 POST/PATCH/DELETE: 비활성",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "paper_mutation_recovery_ko.md"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(destination)


if __name__ == "__main__":
    raise SystemExit(main())
