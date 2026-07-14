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
from trading_agent.paper_operating_mutation_models import PaperSafetyMutationExecution
from trading_agent.paper_operating_session import open_paper_operating_session
from trading_agent.paper_operating_session_models import (
    PaperMutationRecoveryBarrierError,
    PaperOperatingSession,
    PaperPostMutationReconciliationError,
)
from trading_agent.paper_protective_oco_recovery_store import (
    InvalidProtectiveOcoRecoveryError,
    ProtectiveOcoRecoveryConflictError,
)
from trading_agent.paper_risk import PaperRiskConfig
from trading_agent.paper_runtime import PaperRuntimeEpochChangedError
from trading_agent.paper_safety_models import (
    BlockedPaperSafetyPlan,
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyAction,
)
from trading_agent.paper_safety_store import (
    InvalidPaperSafetyPlanError,
    PaperSafetyPlanConflictError,
)
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

SMOKE_RISK_CONFIG = PaperRiskConfig(
    max_risk_dollars=10.0,
    risk_fraction=0.0003333333333333333,
    max_notional_dollars=100.0,
    max_open_positions=1,
    daily_loss_limit_dollars=30.0,
    per_side_cost_bps=20.0,
)
_ACKNOWLEDGED_STATES = frozenset(
    (
        PaperMutationExecutionState.ACKNOWLEDGED,
        PaperMutationExecutionState.ALREADY_ACKNOWLEDGED,
    )
)

type CredentialLoader = Callable[[], AlpacaPaperCredentials]
type SessionOpener = Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    AbstractContextManager[PaperOperatingSession],
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="current-epoch Alpaca Paper cancel·EOD 평탄화를 축소 위험으로 단발 검증"
    )
    parser.add_argument("--arm-paper-mutation", required=True, choices=(PAPER_MUTATION_ARM_VALUE,))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
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
            result = session.execute_safety_actions(arm, SMOKE_RISK_CONFIG)
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
        InvalidPaperSafetyPlanError,
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
        PaperSafetyPlanConflictError,
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
    if isinstance(result, BlockedPaperSafetyPlan):
        _write_report(
            args.output_dir,
            "차단",
            ("current-epoch 안전 게이트가 실행을 차단했습니다",),
        )
        return 1
    state = _execution_state(result)
    _write_execution_report(args.output_dir, state, result)
    return 0 if state in {"acknowledged", "no_action_required"} else 2


def _execution_state(execution: PaperSafetyMutationExecution) -> str:
    if not execution.plan.actions:
        return "no_action_required" if not execution.results else "incomplete"
    if len(execution.results) != len(execution.plan.actions):
        return next(
            (result.state.value for result in execution.results if result.state not in _ACKNOWLEDGED_STATES),
            "incomplete",
        )
    if all(result.state in _ACKNOWLEDGED_STATES for result in execution.results):
        return "acknowledged"
    return next(result.state.value for result in execution.results if result.state not in _ACKNOWLEDGED_STATES)


def _write_execution_report(
    output_dir: Path,
    state: str,
    execution: PaperSafetyMutationExecution,
) -> None:
    action_lines = tuple(
        _action_line(action, _result_state(execution, sequence))
        for sequence, action in enumerate(execution.plan.actions)
    )
    _write_report(
        output_dir,
        state,
        (
            f"단계: {execution.plan.phase.value}",
            f"조치 수: {len(execution.plan.actions)}",
            *(action_lines or ("조치: 없음",)),
            f"reconciled_at: {execution.reconciled_at.isoformat()}",
        ),
    )


def _result_state(execution: PaperSafetyMutationExecution, sequence: int) -> str:
    if sequence >= len(execution.results):
        return "not_attempted"
    return execution.results[sequence].state.value


def _action_line(action: PaperSafetyAction, state: str) -> str:
    match action:
        case PaperCancelOrderAction():
            target = "보호 OCO 취소" if action.protective_oco else "신규진입 주문 취소"
            return f"{action.symbol}: {target} -> {state}"
        case PaperClosePositionAction():
            return f"{action.symbol}: {action.side.value} {action.quantity}주 평탄화 -> {state}"


def _safe_error_reason(error: BaseException) -> str:
    return f"안전 오류 유형: {type(error).__name__}"


def _write_report(output_dir: Path, state: str, details: tuple[str, ...]) -> None:
    lines = (
        "# Alpaca Paper cancel·EOD 평탄화 smoke",
        "",
        "- endpoint: paper-api.alpaca.markets 고정",
        "- live endpoint: 사용 불가",
        "- 최대 notional: 100 USD",
        "- 최대 계획위험: 10 USD",
        "- 최대 포지션: 1",
        "- 일손실 한도: 30 USD",
        "- 편도 비용 가정: 20bp",
        f"- 결과: {state}",
        "- 상세:",
        *(f"  - {detail}" for detail in details),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "paper_safety_mutation_smoke_ko.md"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(destination)


if __name__ == "__main__":
    raise SystemExit(main())
