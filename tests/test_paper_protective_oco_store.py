from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.paper_runtime_fixtures import account
from tests.paper_stream_recovery_fixtures import recovery
from tests.trade_update_ledger_fixtures import OBSERVED_AT, initialized_store, intent
from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
)
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    PaperBrokerState,
    PaperOrderSide,
)
from trading_agent.paper_protective_exit import (
    ProtectiveOcoClientOrderId,
    ProtectiveOcoExitPlan,
)
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoLegKind,
    ProtectiveOcoLegSnapshot,
    ProtectiveOcoOrderType,
    ProtectiveOcoSnapshot,
)
from trading_agent.paper_protective_oco_store import ProtectiveOcoPlanConflictError
from trading_agent.paper_stream_recovery_models import InvalidPaperStreamRecoveryError, PaperRecoveryState
from trading_agent.paper_stream_recovery_runtime import (
    PaperStreamRecoveryIncompleteError,
    build_paper_stream_recovery_observation,
)


def _plan() -> ProtectiveOcoExitPlan:
    return ProtectiveOcoExitPlan(
        client_order_id=ProtectiveOcoClientOrderId("protect-" + "a" * 40),
        parent_intent_id=intent().intent_id,
        symbol="AAA",
        side=PaperOrderSide.SELL,
        quantity=20,
        take_profit_limit=Decimal("10.5"),
        stop_price=Decimal("9.75"),
    )


def _leg(
    kind: ProtectiveOcoLegKind,
) -> ProtectiveOcoLegSnapshot:
    take_profit = kind is ProtectiveOcoLegKind.TAKE_PROFIT
    return ProtectiveOcoLegSnapshot(
        kind=kind,
        broker_order_id=BrokerOrderId("paper-take-profit-1" if take_profit else "paper-stop-1"),
        client_order_id=("protect-" + "a" * 40 if take_profit else "paper-stop-client-1"),
        symbol="AAA",
        side=PaperOrderSide.SELL,
        status="new",
        quantity=Decimal(20),
        filled_quantity=Decimal(0),
        order_type=(ProtectiveOcoOrderType.LIMIT if take_profit else ProtectiveOcoOrderType.STOP),
        limit_price=Decimal("10.5") if take_profit else None,
        stop_price=None if take_profit else Decimal("9.75"),
        time_in_force="day",
        extended_hours=False,
    )


def _snapshot() -> ProtectiveOcoSnapshot:
    return ProtectiveOcoSnapshot(
        observed_at=OBSERVED_AT,
        take_profit=_leg(ProtectiveOcoLegKind.TAKE_PROFIT),
        stop_loss=_leg(ProtectiveOcoLegKind.STOP_LOSS),
    )


def test_protective_oco_plan_is_append_only_and_idempotent(tmp_path: Path) -> None:
    # Given: one bound execution ledger and an exact protective OCO plan.
    store = initialized_store(tmp_path)
    plan = _plan()

    # When: the single writer saves and replays the same plan.
    with store.writer() as writer:
        first = writer.save_protective_oco_plan(plan, OBSERVED_AT)
        replay = writer.save_protective_oco_plan(plan, OBSERVED_AT)

    # Then: one immutable plan row preserves its typed quantities and prices.
    stored = store.protective_oco_plans()
    assert first is True
    assert replay is False
    assert len(stored) == 1
    assert stored[0].plan == plan
    assert stored[0].planned_at == OBSERVED_AT.isoformat()


def test_protective_oco_plan_appends_a_quantity_revision(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    revised = replace(_plan(), quantity=10)

    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
        appended = writer.save_protective_oco_plan(
            revised,
            OBSERVED_AT + dt.timedelta(seconds=1),
        )

    assert appended is True
    assert tuple(stored.plan.quantity for stored in store.protective_oco_plans()) == (
        20,
        10,
    )


def test_protective_oco_plan_rejects_an_identity_rewrite(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)

    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
        with pytest.raises(ProtectiveOcoPlanConflictError):
            _ = writer.save_protective_oco_plan(
                replace(_plan(), stop_price=Decimal("9.50")),
                OBSERVED_AT + dt.timedelta(seconds=1),
            )


def test_recovery_persists_both_nested_oco_legs_under_the_plan(
    tmp_path: Path,
) -> None:
    # Given: a stored protection plan and one broker nested OCO observation.
    store = initialized_store(tmp_path)
    observation = replace(
        recovery(
            epoch="epoch-oco",
            started_at=OBSERVED_AT - dt.timedelta(seconds=1),
            completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
        ),
        protective_ocos=(_snapshot(),),
    )

    # When: the single execution writer commits the recovery checkpoint.
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
        _ = writer.append_paper_stream_recovery(observation)

    # Then: one reconstructed snapshot retains the parent and stop broker IDs.
    stored = store.paper_recovery_protective_ocos()
    assert len(stored) == 1
    assert stored[0].snapshot == _snapshot()


def test_recovery_rejects_a_broker_oco_without_an_immutable_plan(
    tmp_path: Path,
) -> None:
    # Given: Alpaca reports a protective OCO that has no local pre-submit plan.
    store = initialized_store(tmp_path)
    before = PaperOrderStreamHeartbeat(
        PaperStreamEpoch("epoch-unplanned-oco"),
        OBSERVED_AT - dt.timedelta(seconds=3),
        OBSERVED_AT - dt.timedelta(seconds=3),
        OBSERVED_AT - dt.timedelta(seconds=2),
    )
    after = replace(before, pong_at=OBSERVED_AT + dt.timedelta(seconds=2))
    state = PaperRecoveryState(
        PaperBrokerState(account(), (), (), (_snapshot(),)),
        (),
        protective_ocos=(_snapshot(),),
    )

    # When / Then: recovery fails before accepting unowned broker protection.
    with pytest.raises(
        PaperStreamRecoveryIncompleteError,
        match="보호 OCO와 일치하는 immutable 계획",
    ):
        _ = build_paper_stream_recovery_observation(
            before,
            after,
            state,
            store.reconciliation_ledger(),
        )


def test_recovery_rejects_an_oco_with_a_mutated_execution_contract(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    snapshot = replace(
        _snapshot(),
        stop_loss=replace(_snapshot().stop_loss, time_in_force="gtc"),
    )
    before = PaperOrderStreamHeartbeat(
        PaperStreamEpoch("epoch-mutated-oco"),
        OBSERVED_AT - dt.timedelta(seconds=3),
        OBSERVED_AT - dt.timedelta(seconds=3),
        OBSERVED_AT - dt.timedelta(seconds=2),
    )
    after = replace(before, pong_at=OBSERVED_AT + dt.timedelta(seconds=2))
    state = PaperRecoveryState(
        PaperBrokerState(account(), (), (), (snapshot,)),
        (),
        protective_ocos=(snapshot,),
    )
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)

    with pytest.raises(
        PaperStreamRecoveryIncompleteError,
        match="보호 OCO와 일치하는 immutable 계획",
    ):
        _ = build_paper_stream_recovery_observation(
            before,
            after,
            state,
            store.reconciliation_ledger(),
        )


def test_recovery_protective_oco_hash_is_bound_to_the_parent_record(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    observation = replace(
        recovery(
            epoch="epoch-oco-hash",
            started_at=OBSERVED_AT - dt.timedelta(seconds=1),
            completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
        ),
        protective_ocos=(_snapshot(),),
    )
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
        _ = writer.append_paper_stream_recovery(observation)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER paper_stream_recoveries_no_update")
        connection.execute(
            "UPDATE paper_stream_recoveries SET protective_ocos_sha256 = ?",
            ("0" * 64,),
        )
        connection.execute(
            "CREATE TRIGGER paper_stream_recoveries_no_update "
            "BEFORE UPDATE ON paper_stream_recoveries "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )

    with pytest.raises(InvalidPaperStreamRecoveryError):
        _ = store.paper_stream_recoveries()
