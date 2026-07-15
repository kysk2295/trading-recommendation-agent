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
from trading_agent.paper_account_activity_store import (
    InvalidPaperAccountActivityError,
    PaperAccountActivityConflictError,
)
from trading_agent.paper_execution_models import IntentId
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
from trading_agent.paper_operating_mutation_models import (
    PaperProtectiveCancelMutationExecution,
)
from trading_agent.paper_operating_session import open_paper_operating_session
from trading_agent.paper_operating_session_models import (
    PaperMutationRecoveryBarrierError,
    PaperOperatingSession,
    PaperPostMutationReconciliationError,
)
from trading_agent.paper_protective_exit import BlockedProtectiveExitPlan, NoProtectiveExitRequired
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
from trading_agent.trade_update_receipts import (
    InvalidTradeUpdateRawReceiptError,
    TradeUpdateReceiptConflictError,
    UnknownTradeUpdateReceiptError,
)

type CredentialLoader = Callable[[], AlpacaPaperCredentials]
type SessionOpener = Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    AbstractContextManager[PaperOperatingSession],
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="체결된 Alpaca Paper entry에 대한 보호 OCO를 current-epoch에서 제출")
    parser.add_argument("--arm-paper-mutation", required=True, choices=(PAPER_MUTATION_ARM_VALUE,))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--intent-id", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_loader: CredentialLoader = load_alpaca_paper_credentials,
    session_opener: SessionOpener = open_paper_operating_session,
) -> int:
    args = _parser().parse_args(argv)
    arm = PaperMutationArm(args.arm_paper_mutation)
    store = ExecutionStore(args.database)
    if not store.is_initialized():
        _write_report(args.output_dir, "차단", ("결합된 실행 원장이 없습니다",))
        return 1
    try:
        with session_opener(credential_loader(), store) as session:
            result = session.execute_protective_oco(IntentId(args.intent_id), arm)
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
        rendered = str(error)
        print(rendered, file=sys.stderr)
        _write_report(args.output_dir, "오류", (rendered,))
        return 2
    if isinstance(result, BlockedProtectiveExitPlan):
        _write_report(args.output_dir, "차단", result.reasons)
        return 1
    if isinstance(result, NoProtectiveExitRequired):
        _write_report(
            args.output_dir,
            "no_protective_exit_required",
            ("현재 broker 포지션은 이미 정확히 보호되었거나 남은 포지션이 없습니다",),
        )
        return 0
    if isinstance(result, PaperProtectiveCancelMutationExecution):
        _write_report(
            args.output_dir,
            "incomplete",
            (
                f"cancel_stage: {result.result.state.value}",
                "replacement OCO는 다음 current-epoch 실행에서만 제출합니다",
                f"reconciled_at: {result.reconciled_at.isoformat()}",
            ),
        )
        return 2
    state = result.result.state
    _write_report(
        args.output_dir,
        state.value,
        (
            f"parent_intent: {result.plan.parent_intent_id}",
            f"symbol: {result.plan.symbol}",
            f"quantity: {result.plan.quantity}",
            f"stop: {result.plan.stop_price:.2f}",
            f"target_2r: {result.plan.take_profit_limit:.2f}",
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


def _write_report(output_dir: Path, state: str, details: tuple[str, ...]) -> None:
    lines = (
        "# Alpaca Paper 보호 OCO smoke",
        "",
        "- endpoint: paper-api.alpaca.markets 고정",
        "- live endpoint: 사용 불가",
        "- 실행 조건: current-epoch 복구와 정확한 parent intent 체결 노출",
        "- 주문 종류: DAY OCO stop-market + 2R limit",
        f"- 결과: {state}",
        "- 상세:",
        *(f"  - {detail}" for detail in details),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "paper_protective_oco_smoke_ko.md"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(destination)


if __name__ == "__main__":
    raise SystemExit(main())
