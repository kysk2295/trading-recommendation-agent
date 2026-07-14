from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    OTHER_FINGERPRINT,
    initialized_store,
)
from trading_agent.execution_errors import AccountBindingConflictError
from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSide
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.paper_safety_store import InvalidPaperSafetyPlanError


def _plan(
    phase: PaperSafetyPhase = PaperSafetyPhase.KILL_SWITCH,
) -> PaperSafetyPlan:
    return PaperSafetyPlan(
        account_fingerprint=FINGERPRINT,
        observed_at=OBSERVED_AT,
        session_date=dt.date(2026, 7, 14),
        phase=phase,
        mark_to_market_daily_pnl=Decimal("-226"),
        conservative_daily_pnl=Decimal("-301"),
        actions=(
            PaperCancelOrderAction(BrokerOrderId("entry-1"), "AAA", False),
            PaperCancelOrderAction(BrokerOrderId("oco-parent-1"), "AAA", True),
            PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal(10)),
        ),
    )


def test_safety_plan_is_atomic_append_only_typed_and_idempotent(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    plan = _plan()

    with store.writer() as writer:
        first = writer.save_paper_safety_plan(plan)
        replay = writer.save_paper_safety_plan(plan)

    stored = store.paper_safety_plans()
    assert first is True
    assert replay is False
    assert len(stored) == 1
    assert stored[0].plan == plan
    assert store.reconciliation_ledger().paper_safety_plans == stored
    with (
        sqlite3.connect(store.path) as connection,
        pytest.raises(
            sqlite3.IntegrityError,
            match="append-only",
        ),
    ):
        _ = connection.execute("UPDATE paper_safety_plans SET phase = 'eod_flatten'")


def test_safety_plan_binds_to_the_execution_account(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)

    with store.writer() as writer, pytest.raises(AccountBindingConflictError):
        _ = writer.save_paper_safety_plan(
            PaperSafetyPlan(
                account_fingerprint=OTHER_FINGERPRINT,
                observed_at=_plan().observed_at,
                session_date=_plan().session_date,
                phase=_plan().phase,
                mark_to_market_daily_pnl=_plan().mark_to_market_daily_pnl,
                conservative_daily_pnl=_plan().conservative_daily_pnl,
                actions=_plan().actions,
            )
        )


def test_monitoring_phase_is_not_persisted_as_a_safety_action_plan(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)

    with store.writer() as writer, pytest.raises(InvalidPaperSafetyPlanError):
        _ = writer.save_paper_safety_plan(_plan(PaperSafetyPhase.MONITORING))


def test_entry_cutoff_plan_cannot_cancel_protection_or_flatten(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)

    with store.writer() as writer, pytest.raises(InvalidPaperSafetyPlanError):
        _ = writer.save_paper_safety_plan(replace(_plan(), phase=PaperSafetyPhase.ENTRY_CUTOFF))
