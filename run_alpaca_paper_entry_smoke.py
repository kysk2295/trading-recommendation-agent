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
from trading_agent.paper_account_activity_store import (
    InvalidPaperAccountActivityError,
    PaperAccountActivityConflictError,
)
from trading_agent.paper_execution_models import IntentId, PaperOrderIntent, PaperOrderSide
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
)
from trading_agent.paper_order_gate_models import BlockedPaperOrderGateDecision, LatestCompletedBar
from trading_agent.paper_protective_oco_recovery_store import (
    InvalidProtectiveOcoRecoveryError,
    ProtectiveOcoRecoveryConflictError,
)
from trading_agent.paper_risk import PaperRiskConfig
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

SMOKE_RISK_CONFIG = PaperRiskConfig(
    max_risk_dollars=10.0,
    risk_fraction=0.0003333333333333333,
    max_notional_dollars=100.0,
    max_open_positions=1,
    daily_loss_limit_dollars=30.0,
    per_side_cost_bps=20.0,
)

type CredentialLoader = Callable[[], AlpacaPaperCredentials]
type SessionOpener = Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    AbstractContextManager[PaperOperatingSession],
]


def _aware_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timezone offset이 필요합니다")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="현재 1분봉 후보 하나를 최대 100 USD Alpaca Paper 주문으로 검증")
    parser.add_argument("--arm-paper-mutation", required=True, choices=(PAPER_MUTATION_ARM_VALUE,))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--intent-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", choices=("buy", "sell"), default="buy")
    parser.add_argument("--entry-limit", type=float, required=True)
    parser.add_argument("--stop", type=float, required=True)
    parser.add_argument("--target-1r", type=float, required=True)
    parser.add_argument("--target-2r", type=float, required=True)
    parser.add_argument("--created-at", type=_aware_datetime, required=True)
    parser.add_argument("--bar-start", type=_aware_datetime, required=True)
    parser.add_argument("--bar-first-observed", type=_aware_datetime, required=True)
    parser.add_argument("--liquidity-quantity", type=int, required=True)
    parser.add_argument("--spread-bps", type=float, required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_loader: CredentialLoader = load_alpaca_paper_credentials,
    session_opener: SessionOpener = open_paper_operating_session,
) -> int:
    args = _parser().parse_args(argv)
    store = ExecutionStore(args.database)
    if not store.is_initialized():
        _write_report(args.output_dir, "차단", ("결합된 실행 원장이 없습니다",))
        return 1
    request = _request(args)
    try:
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
        InvalidPaperStreamRecoveryError,
        InvalidProtectiveOcoRecoveryError,
        InvalidTradeUpdateRawReceiptError,
        MissingAlpacaPaperCredentialsError,
        PaperAccountActivityConflictError,
        PaperActivityHistoryIncompleteError,
        PaperMutationConflictError,
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
        rendered = str(error)
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


def _request(args: argparse.Namespace) -> PaperOrderAdmissionRequest:
    intent = PaperOrderIntent(
        IntentId(args.intent_id),
        "orb",
        "paper-smoke-v1",
        args.symbol,
        args.created_at,
        PaperOrderSide(args.side),
        args.entry_limit,
        args.stop,
        args.target_1r,
        args.target_2r,
    )
    return PaperOrderAdmissionRequest(
        LatestCompletedBar(args.symbol, args.bar_start, args.bar_first_observed),
        intent,
        args.liquidity_quantity,
        args.spread_bps,
        SMOKE_RISK_CONFIG,
    )


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
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "paper_entry_smoke_ko.md"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(destination)


if __name__ == "__main__":
    raise SystemExit(main())
