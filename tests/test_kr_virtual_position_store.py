from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from tests.test_autonomous_task_models import task_fixture
from tests.test_kr_virtual_position_engine import _recommendation
from trading_agent.autonomous_kr_tool_runtime import (
    InvalidKrVirtualStartupReconciliationError,
    KrAutonomousToolServices,
    kr_tool_bindings,
)
from trading_agent.autonomous_reasoning import AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousTaskId
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext, AutonomousToolRuntime
from trading_agent.kr_autonomous_trade_models import KrTradeRecommendation, event_id
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_virtual_position_engine import arm_kr_virtual_position
from trading_agent.kr_virtual_position_models import KrVirtualPositionEvent, virtual_position_event_id
from trading_agent.kr_virtual_position_store import InvalidKrVirtualPositionStoreError, KrVirtualPositionStore


def test_store_reconstructs_independent_positions_and_exact_replay(tmp_path: Path) -> None:
    # Given: two tasks independently attempt the same symbol and theme.
    first = _recommendation()
    first_event = arm_kr_virtual_position(first, first.timestamp)
    second_event = _other_task_event(first_event)
    store = KrVirtualPositionStore(tmp_path / "positions.sqlite3")

    # When: both chains append and the first event is replayed.
    assert store.append(first_event)
    assert store.append(second_event)
    replay = store.append(first_event)

    # Then: replay is idempotent and both open positions reconstruct separately.
    assert replay is False
    assert store.events(first_event.position_id) == (first_event,)
    assert {event.position_id for event in store.open_positions()} == {
        first_event.position_id,
        second_event.position_id,
    }
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"
    assert oct(store.path.parent.stat().st_mode & 0o777) == "0o700"


def test_store_rejects_divergent_chain_tamper_and_private_file_attacks(tmp_path: Path) -> None:
    # Given: one durable position chain.
    event = arm_kr_virtual_position(_recommendation(), _recommendation().timestamp)
    store = KrVirtualPositionStore(tmp_path / "positions.sqlite3")
    assert store.append(event)

    # When/Then: a divergent sequence and schema mutation are rejected.
    divergent_draft = event.model_copy(update={"sequence": 2, "event_id": ""})
    divergent = KrVirtualPositionEvent.model_validate(
        divergent_draft.model_copy(update={"event_id": virtual_position_event_id(divergent_draft)}).model_dump(
            mode="python"
        )
    )
    with pytest.raises(InvalidKrVirtualPositionStoreError):
        _ = store.append(divergent)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER kr_virtual_position_events_no_update")
    with pytest.raises(InvalidKrVirtualPositionStoreError):
        _ = store.open_positions()

    linked = tmp_path / "linked.sqlite3"
    os.link(store.path, linked)
    with pytest.raises(InvalidKrVirtualPositionStoreError):
        _ = KrVirtualPositionStore(linked).open_positions()
    symlink = tmp_path / "symlink.sqlite3"
    symlink.symlink_to(store.path)
    with pytest.raises(InvalidKrVirtualPositionStoreError):
        _ = KrVirtualPositionStore(symlink).open_positions()
    public = tmp_path / "public.sqlite3"
    public.touch(mode=0o644)
    with pytest.raises(InvalidKrVirtualPositionStoreError):
        _ = KrVirtualPositionStore(public).open_positions()


def test_execute_and_reconcile_tools_are_restart_safe_and_task_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an approved current recommendation and its durable task lineage.
    task = task_fixture()
    recommendation = _recommendation_for_task(task.task_id)
    services = KrAutonomousToolServices(
        tmp_path / "browser.sqlite3",
        tmp_path / "signals.sqlite3",
        tmp_path / "tasks.sqlite3",
        "{}",
    )
    assert services.trade_database is not None and services.position_database is not None
    assert KrAutonomousTradeStore(services.trade_database).append(recommendation)
    with AutonomousTaskStore(services.task_database).writer() as writer:
        assert writer.create_task(task)
    context = AutonomousToolExecutionContext(
        task_id=task.task_id,
        agent_family_id=task.agent_family_id,
        market_scope=task.market_scope,
    )
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.utc_now", lambda: recommendation.timestamp)
    runtime = AutonomousToolRuntime(kr_tool_bindings(services), lambda: recommendation.timestamp)

    # When: Trading executes twice across a restart and Position reconciles it.
    first = runtime.dispatch(
        AutonomousAgentRole.TRADING,
        _call("kr.virtual.execute", {"recommendation_id": recommendation.event_id}),
        context,
    )
    replay = AutonomousToolRuntime(kr_tool_bindings(services), lambda: recommendation.timestamp).dispatch(
        AutonomousAgentRole.TRADING,
        _call("kr.virtual.execute", {"recommendation_id": recommendation.event_id}),
        context,
    )
    position_id = json.loads(first.bounded_json)["position_id"]
    reconciled = runtime.dispatch(
        AutonomousAgentRole.POSITION,
        _call("kr.position.reconcile", {"position_id": position_id}),
        context,
    )

    # Then: exact replay creates one ARMED event and reconciliation preserves it.
    assert replay.bounded_json == first.bounded_json
    assert json.loads(reconciled.bounded_json)["state"] == "ARMED"
    assert len(KrVirtualPositionStore(services.position_database).events(position_id)) == 1


def test_startup_reconciles_open_positions_before_return_and_restart_is_exact(tmp_path: Path) -> None:
    # Given: durable task, approved recommendation, and one open ARMED position.
    task = task_fixture()
    recommendation = _recommendation_for_task(task.task_id)
    paths = _service_paths(tmp_path)
    initial = KrAutonomousToolServices(*paths, startup_at=recommendation.timestamp)
    assert initial.trade_database is not None and initial.position_database is not None
    with AutonomousTaskStore(initial.task_database).writer() as writer:
        assert writer.create_task(task)
    assert KrAutonomousTradeStore(initial.trade_database).append(recommendation)
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    assert KrVirtualPositionStore(initial.position_database).append(armed)

    # When: service construction occurs at expiry and then repeats after restart.
    reconciled = KrAutonomousToolServices(*paths, startup_at=recommendation.valid_until)
    restarted = KrAutonomousToolServices(*paths, startup_at=recommendation.valid_until)

    # Then: construction terminalizes before returning and restart appends nothing.
    assert reconciled.startup_reconciliation.open_position_count == 1
    assert reconciled.startup_reconciliation.appended_event_count == 1
    assert restarted.startup_reconciliation.open_position_count == 0
    assert restarted.startup_reconciliation.appended_event_count == 0
    assert reconciled.position_database is not None
    events = KrVirtualPositionStore(reconciled.position_database).events(armed.position_id)
    assert tuple(event.state.value for event in events) == ("ARMED", "EXPIRED")


def test_startup_reconciliation_is_noop_without_open_positions(tmp_path: Path) -> None:
    # Given/When: a fresh production service boundary is constructed.
    services = KrAutonomousToolServices(*_service_paths(tmp_path), startup_at=_recommendation().timestamp)

    # Then: startup reports an exact no-op and does not create a position database.
    assert services.startup_reconciliation.open_position_count == 0
    assert services.startup_reconciliation.appended_event_count == 0
    assert services.position_database is not None and not services.position_database.exists()


def test_startup_reconciliation_rejects_missing_durable_task_lineage(tmp_path: Path) -> None:
    # Given: a recommendation and open position whose durable task record is absent.
    recommendation = _recommendation_for_task(task_fixture().task_id)
    paths = _service_paths(tmp_path)
    initial = KrAutonomousToolServices(*paths, startup_at=recommendation.timestamp)
    assert initial.trade_database is not None and initial.position_database is not None
    assert KrAutonomousTradeStore(initial.trade_database).append(recommendation)
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    positions = KrVirtualPositionStore(initial.position_database)
    assert positions.append(armed)

    # When/Then: construction fails closed before appending a position event.
    with pytest.raises(InvalidKrVirtualStartupReconciliationError):
        _ = KrAutonomousToolServices(*paths, startup_at=recommendation.valid_until)
    assert positions.events(armed.position_id) == (armed,)


def _other_task_event(event: KrVirtualPositionEvent) -> KrVirtualPositionEvent:
    draft = event.model_copy(update={"task_id": "f" * 64, "position_id": "e" * 64, "event_id": ""})
    return KrVirtualPositionEvent.model_validate(
        draft.model_copy(update={"event_id": virtual_position_event_id(draft)}).model_dump(mode="python")
    )


def _recommendation_for_task(task_id: AutonomousTaskId) -> KrTradeRecommendation:
    draft = _recommendation().model_copy(update={"task_id": task_id, "event_id": ""})
    return KrTradeRecommendation.model_validate(
        draft.model_copy(update={"event_id": event_id(draft)}).model_dump(mode="python")
    )


def _call(name: str, values: dict[str, str]) -> AutonomousToolCall:
    return AutonomousToolCall(
        tool_name=name,
        args=AutonomousToolArguments(values),
        reason="Exercise one bounded KR virtual-position tool contract.",
    )


def _service_paths(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    return (
        tmp_path / "browser.sqlite3",
        tmp_path / "signals.sqlite3",
        tmp_path / "tasks.sqlite3",
        "{}",
    )
