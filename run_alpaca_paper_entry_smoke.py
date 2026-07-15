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
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
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
from trading_agent.lane_defaults import INTRADAY_PILOT_PAPER_RISK_CONFIG
from trading_agent.paper_account_activity_store import (
    InvalidPaperAccountActivityError,
    PaperAccountActivityConflictError,
)
from trading_agent.paper_entry_source import (
    InvalidCurrentOrbPaperEntrySourceError,
    load_current_orb_paper_entry,
)
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE, PaperMutationArm
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionState
from trading_agent.paper_mutation_recovery import (
    InvalidPaperMutationRecoverySnapshotError,
    PaperMutationRecoveryAccountError,
)
from trading_agent.paper_mutation_store import (
    InvalidPaperMutationTransitionError,
    PaperMutationConflictError,
)
from trading_agent.paper_mutation_validation import InvalidPaperMutationRecordError
from trading_agent.paper_operating_session import open_paper_operating_session
from trading_agent.paper_operating_session_models import (
    PaperMutationRecoveryBarrierError,
    PaperOperatingSession,
    PaperOrderAdmissionRequest,
    PaperPostMutationReconciliationError,
)
from trading_agent.paper_order_gate_models import BlockedPaperOrderGateDecision
from trading_agent.paper_protective_oco_recovery_store import (
    InvalidProtectiveOcoRecoveryError,
    ProtectiveOcoRecoveryConflictError,
)
from trading_agent.paper_runtime import PaperRuntimeEpochChangedError
from trading_agent.paper_stream_recovery import (
    InvalidPaperStreamRecoveryError,
    PaperStreamRecoveryConflictError,
)
from trading_agent.paper_stream_recovery_runtime import PaperStreamRecoveryIncompleteError
from trading_agent.private_report import write_private_report
from trading_agent.trade_update_receipts import (
    InvalidTradeUpdateRawReceiptError,
    TradeUpdateReceiptConflictError,
    UnknownTradeUpdateReceiptError,
)

SMOKE_RISK_CONFIG = INTRADAY_PILOT_PAPER_RISK_CONFIG

type CredentialLoader = Callable[[], AlpacaPaperCredentials]
type SessionOpener = Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    AbstractContextManager[PaperOperatingSession],
]
type SourceLoader = Callable[[Path, dt.datetime], PaperOrderAdmissionRequest]
type Clock = Callable[[], dt.datetime]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="watch SQLite의 현재 ORB 후보 하나를 최대 100 USD Alpaca Paper 주문으로 검증"
    )
    parser.add_argument("--arm-paper-mutation", required=True, choices=(PAPER_MUTATION_ARM_VALUE,))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--watch-database", type=Path, required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_loader: CredentialLoader = load_alpaca_paper_credentials,
    session_opener: SessionOpener = open_paper_operating_session,
    source_loader: SourceLoader = load_current_orb_paper_entry,
    clock: Clock = _now,
) -> int:
    args = _parser().parse_args(argv)
    store = ExecutionStore(args.database)
    if not store.is_initialized():
        _write_report(args.output_dir, "차단", ("결합된 실행 원장이 없습니다",))
        return 1
    try:
        request = source_loader(args.watch_database, clock())
        with session_opener(credential_loader(), store) as session:
            result = session.execute_entry(
                request,
                PaperMutationArm(args.arm_paper_mutation),
            )
    except (
        AccountBindingConflictError,
        AlpacaApiError,
        AlpacaPaperSecretEncodingError,
        AlpacaPaperSecretFileError,
        ExecutionSchemaIntegrityError,
        InvalidPaperAccountActivityError,
        InvalidPaperMutationRecordError,
        InvalidPaperMutationRecoverySnapshotError,
        InvalidPaperMutationTransitionError,
        InvalidCurrentOrbPaperEntrySourceError,
        InvalidPaperStreamRecoveryError,
        InvalidProtectiveOcoRecoveryError,
        InvalidTradeUpdateRawReceiptError,
        MissingAlpacaPaperCredentialsError,
        PaperAccountActivityConflictError,
        PaperActivityHistoryIncompleteError,
        PaperMutationConflictError,
        PaperMutationRecoveryAccountError,
        PaperMutationRecoveryBarrierError,
        PaperPostMutationReconciliationError,
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
        _write_report(args.output_dir, "오류", (rendered,))
        return 2
    if isinstance(result, BlockedPaperOrderGateDecision):
        _write_report(args.output_dir, "차단", result.reasons)
        return 1
    state = result.result.state
    _write_report(
        args.output_dir,
        state.value,
        (
            f"intent: {result.approval.sized_order.intent.intent_id}",
            f"symbol: {result.approval.sized_order.intent.symbol}",
            f"notional: {result.approval.sized_order.notional:.2f} USD",
            f"reconciled_at: {result.reconciled_at.isoformat()}",
        ),
    )
    return (
        0
        if state
        in {
            PaperMutationExecutionState.ACKNOWLEDGED,
            PaperMutationExecutionState.ALREADY_ACKNOWLEDGED,
        }
        else 2
    )


def _safe_error_reason(error: BaseException) -> str:
    return f"안전 오류 유형: {type(error).__name__}"


def _write_report(output_dir: Path, state: str, details: tuple[str, ...]) -> None:
    lines = (
        "# Alpaca Paper 단발 진입 smoke",
        "",
        "- endpoint: paper-api.alpaca.markets 고정",
        "- live endpoint: 사용 불가",
        "- 최대 notional: 100 USD",
        "- 최대 계획위험: 10 USD",
        f"- 결과: {state}",
        "- 상세:",
        *(f"  - {detail}" for detail in details),
    )
    destination = output_dir / "paper_entry_smoke_ko.md"
    write_private_report(destination, "\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
