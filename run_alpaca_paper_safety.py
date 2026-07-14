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
from trading_agent.paper_protective_oco_recovery_store import (
    InvalidProtectiveOcoRecoveryError,
    ProtectiveOcoRecoveryConflictError,
)
from trading_agent.paper_runtime import PaperRuntimeEpochChangedError
from trading_agent.paper_safety_models import (
    BlockedPaperSafetyPlan,
    PaperCancelOrderAction,
    PaperSafetyAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
    PaperSafetyPlanDecision,
)
from trading_agent.paper_safety_store import (
    InvalidPaperSafetyPlanError,
    PaperSafetyPlanConflictError,
)
from trading_agent.paper_stream_recovery import (
    InvalidPaperStreamRecoveryError,
    PaperStreamRecoveryConflictError,
)
from trading_agent.paper_stream_recovery_runtime import (
    PaperStreamRecoveryIncompleteError,
)
from trading_agent.paper_trade_update_runtime import plan_current_paper_safety
from trading_agent.trade_update_receipts import (
    InvalidTradeUpdateRawReceiptError,
    TradeUpdateReceiptConflictError,
    UnknownTradeUpdateReceiptError,
)

type CredentialLoader = Callable[[], AlpacaPaperCredentials]
type SafetyPlanLoader = Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    PaperSafetyPlanDecision,
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpaca Paper WSS·GET current-epoch 상태로 안전조치 계획만 생성")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("outputs/paper_execution/paper_execution.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/paper_execution/safety/latest"),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_loader: CredentialLoader = load_alpaca_paper_credentials,
    plan_loader: SafetyPlanLoader = plan_current_paper_safety,
) -> int:
    args = _parser().parse_args(argv)
    store = ExecutionStore(args.database)
    if not store.path.is_file():
        decision = BlockedPaperSafetyPlan(("실행 원장이 초기화되지 않았습니다",))
        _write_report(args.output_dir, decision)
        return 1
    try:
        with store.writer():
            pass
        decision = plan_loader(credential_loader(), store)
    except (
        AccountBindingConflictError,
        AlpacaApiError,
        AlpacaPaperSecretEncodingError,
        AlpacaPaperSecretFileError,
        ExecutionSchemaIntegrityError,
        InvalidPaperAccountActivityError,
        InvalidPaperSafetyPlanError,
        InvalidPaperStreamRecoveryError,
        InvalidProtectiveOcoRecoveryError,
        InvalidTradeUpdateRawReceiptError,
        MissingAlpacaPaperCredentialsError,
        PaperAccountActivityConflictError,
        PaperActivityHistoryIncompleteError,
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
        rendered = str(error)
        print(rendered, file=sys.stderr)
        _write_report(args.output_dir, BlockedPaperSafetyPlan((rendered,)))
        return 2
    _write_report(args.output_dir, decision)
    return 1 if isinstance(decision, BlockedPaperSafetyPlan) else 0


def _write_report(
    output_dir: Path,
    decision: PaperSafetyPlanDecision,
) -> None:
    if isinstance(decision, BlockedPaperSafetyPlan):
        details = (
            "- 안전조치 계획: 차단",
            "- 사유:",
            *(f"  - {reason}" for reason in decision.reasons),
        )
    else:
        details = _plan_lines(decision)
    lines = (
        "# Alpaca Paper current-epoch 안전조치 계획",
        "",
        *details,
        "- 외부 동작: Alpaca Paper WSS + REST GET only",
        "- 로컬 동작: current-epoch 복구와 append-only 안전계획 원장 저장",
        "- 주문 POST/PATCH/DELETE: 비활성",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "paper_safety_plan_ko.md"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(destination)


def _plan_lines(plan: PaperSafetyPlan) -> tuple[str, ...]:
    phase = {
        PaperSafetyPhase.MONITORING: "monitoring",
        PaperSafetyPhase.ENTRY_CUTOFF: "entry cutoff",
        PaperSafetyPhase.KILL_SWITCH: "kill switch",
        PaperSafetyPhase.EOD_FLATTEN: "EOD flatten",
    }[plan.phase]
    actions = tuple(_action_line(action) for action in plan.actions)
    return (
        "- 안전조치 계획: 생성",
        f"- 관측 시각: {plan.observed_at.isoformat()}",
        f"- 뉴욕 거래일: {plan.session_date.isoformat()}",
        f"- 단계: {phase}",
        f"- MTM 일손익: {plan.mark_to_market_daily_pnl} USD",
        f"- 보수적 일손익: {plan.conservative_daily_pnl} USD",
        f"- 조치 수: {len(actions)}",
        "- 조치:",
        *(f"  - {action}" for action in (actions or ("없음",))),
    )


def _action_line(action: PaperSafetyAction) -> str:
    if isinstance(action, PaperCancelOrderAction):
        target = "보호 OCO 취소" if action.protective_oco else "신규진입 주문 취소"
        return f"{action.symbol}: {target}"
    return f"{action.symbol}: {action.side.value} {action.quantity}주 평탄화"


if __name__ == "__main__":
    raise SystemExit(main())
