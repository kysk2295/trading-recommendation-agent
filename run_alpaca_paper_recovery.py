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
    UnboundExecutionAccountError,
    UnsupportedExecutionSchemaError,
)
from trading_agent.execution_store import (
    ExecutionStore,
    WriterLeaseUnavailableError,
)
from trading_agent.paper_account_activity_store import (
    InvalidPaperAccountActivityError,
    PaperAccountActivityConflictError,
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
    PaperTradeUpdateRecoveryProbe,
    PaperTradeUpdateRecoveryProbeError,
    probe_paper_trade_update_recovery,
)
from trading_agent.trade_update_receipts import (
    InvalidTradeUpdateRawReceiptError,
    TradeUpdateReceiptConflictError,
    UnknownTradeUpdateReceiptError,
)

type CredentialLoader = Callable[[], AlpacaPaperCredentials]
type RecoveryProbeLoader = Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    PaperTradeUpdateRecoveryProbe,
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpaca paper 주문 스트림 재연결과 REST 복구를 GET-only로 검증")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("outputs/paper_execution/paper_execution.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/paper_execution/recovery/latest"),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_loader: CredentialLoader = load_alpaca_paper_credentials,
    probe_loader: RecoveryProbeLoader = probe_paper_trade_update_recovery,
) -> int:
    args = _parser().parse_args(argv)
    store = ExecutionStore(args.database)
    if not store.is_initialized():
        _write_report(args.output_dir, None, ("실행 원장이 초기화되지 않았습니다",))
        return 1
    try:
        result = probe_loader(credential_loader(), store)
    except (
        AccountBindingConflictError,
        AlpacaApiError,
        AlpacaPaperSecretEncodingError,
        AlpacaPaperSecretFileError,
        ExecutionSchemaIntegrityError,
        InvalidPaperStreamRecoveryError,
        InvalidPaperAccountActivityError,
        InvalidProtectiveOcoRecoveryError,
        InvalidTradeUpdateRawReceiptError,
        MissingAlpacaPaperCredentialsError,
        PaperOrderReadIncompleteError,
        PaperActivityHistoryIncompleteError,
        PaperAccountActivityConflictError,
        PaperOrderStreamError,
        PaperRuntimeEpochChangedError,
        ProtectiveOcoRecoveryConflictError,
        PaperStreamRecoveryConflictError,
        PaperStreamRecoveryIncompleteError,
        PaperTradeUpdateRecoveryProbeError,
        TradeUpdateReceiptConflictError,
        UnboundExecutionAccountError,
        UnknownTradeUpdateReceiptError,
        UnsupportedExecutionSchemaError,
        WriterLeaseUnavailableError,
        httpx2.HTTPError,
        OSError,
        sqlite3.Error,
    ) as error:
        rendered = _safe_error_reason(error)
        print(rendered, file=sys.stderr)
        _write_report(args.output_dir, None, (rendered,))
        return 2
    detail_reasons = (
        ("REST 누적 체결은 복구됐지만 개별 execution 상세는 추가 복구가 필요합니다",)
        if not result.execution_detail_complete
        else ()
    )
    reasons = (
        *result.blocking_reasons,
        *detail_reasons,
    )
    _write_report(args.output_dir, result, reasons)
    return 1 if result.blocking_reasons else 0


def _safe_error_reason(error: BaseException) -> str:
    return f"안전 오류 유형: {type(error).__name__}"


def _write_report(
    output_dir: Path,
    result: PaperTradeUpdateRecoveryProbe | None,
    reasons: tuple[str, ...],
) -> None:
    lines = [
        "# Alpaca Paper 주문 스트림 REST 복구",
        "",
        f"- snapshot 저장: {'완료' if result is not None else '실패'}",
        (f"- 복구 완료시각: {result.completed_at}" if result is not None else "- 복구 완료시각: 없음"),
        (
            f"- 정규화 주문 snapshot: {result.recovery_order_count}건"
            if result is not None
            else "- 정규화 주문 snapshot: 0건"
        ),
        (
            f"- Account Activities FILL: {result.recovery_activity_count}건"
            if result is not None
            else "- Account Activities FILL: 0건"
        ),
        (
            f"- 보호 OCO snapshot: {result.recovery_protective_oco_count}건"
            if result is not None
            else "- 보호 OCO snapshot: 0건"
        ),
        (
            "- 개별 execution 상세: 완전"
            if result is not None and result.execution_detail_complete
            else "- 개별 execution 상세: 불완전 또는 미검증"
        ),
        "- 외부 동작: Alpaca Paper WSS + REST GET only",
        "- 주문 POST/PATCH/DELETE: 비활성",
        (
            "- 신규 주문 admission: 차단"
            if result is None or result.blocking_reasons
            else "- 신규 주문 admission: 미평가 (세션 종료 후 재사용 불가)"
        ),
        "- 사유:",
        *(f"  - {reason}" for reason in (reasons or ("없음",))),
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "paper_stream_recovery_ko.md"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(destination)


if __name__ == "__main__":
    raise SystemExit(main())
