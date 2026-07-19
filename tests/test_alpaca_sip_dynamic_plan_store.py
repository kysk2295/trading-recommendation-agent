from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from pathlib import Path

import pytest

from tests.test_alpaca_sip_dynamic_subscription import _candidates, _decision, _identity
from trading_agent.alpaca_sip_dynamic_plan_store import (
    AlpacaSipDynamicPlanStore,
    AlpacaSipDynamicPlanStoreError,
)
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_subscription_models import BroadScannerSnapshot
from trading_agent.us_subscription_policy_state import advance_subscription_policy_state

NOW = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)


def test_store_reuses_exact_plan_across_unchanged_runtime_states(tmp_path: Path) -> None:
    first_decision = _decision(NOW)
    first_state = advance_subscription_policy_state(None, first_decision)
    later = NOW + dt.timedelta(minutes=1)
    next_decision = build_subscription_policy_decision(
        BroadScannerSnapshot(_identity(), later - dt.timedelta(seconds=10), _candidates()),
        evaluated_at=later,
        active=first_state.active,
        cooldowns=first_state.cooldowns,
        config=first_decision.config,
    )
    next_state = advance_subscription_policy_state(first_state, next_decision)
    store = AlpacaSipDynamicPlanStore(tmp_path / "dynamic-plans.sqlite3")

    first = store.roll(first_state)
    replay = store.roll(next_state)

    assert first.appended is True
    assert replay.appended is False
    assert replay.plan == first.plan
    assert store.latest() == first.plan
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_store_appends_new_plan_when_market_date_changes(tmp_path: Path) -> None:
    first_state = advance_subscription_policy_state(None, _decision(NOW))
    store = AlpacaSipDynamicPlanStore(tmp_path / "dynamic-plans.sqlite3")
    first = store.roll(first_state)
    next_session = NOW + dt.timedelta(days=3)
    next_decision = build_subscription_policy_decision(
        BroadScannerSnapshot(_identity(), next_session - dt.timedelta(seconds=10), _candidates()),
        evaluated_at=next_session,
        active=first_state.active,
        cooldowns=first_state.cooldowns,
        config=_decision(NOW).config,
    )
    next_state = advance_subscription_policy_state(first_state, next_decision)

    rolled = store.roll(next_state)

    assert rolled.appended is True
    assert rolled.plan.plan_id != first.plan.plan_id
    assert store.latest() == rolled.plan


def test_store_rejects_tampered_latest_payload(tmp_path: Path) -> None:
    store = AlpacaSipDynamicPlanStore(tmp_path / "dynamic-plans.sqlite3")
    _ = store.roll(advance_subscription_policy_state(None, _decision(NOW)))
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER dynamic_plan_no_update")
        connection.execute("UPDATE dynamic_plan SET payload_json=X'7B7D'")
        connection.commit()

    with pytest.raises(AlpacaSipDynamicPlanStoreError):
        _ = store.latest()


def test_store_rejects_missing_append_only_trigger(tmp_path: Path) -> None:
    store = AlpacaSipDynamicPlanStore(tmp_path / "dynamic-plans.sqlite3")
    _ = store.roll(advance_subscription_policy_state(None, _decision(NOW)))
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER dynamic_plan_no_delete")
        connection.commit()

    with pytest.raises(AlpacaSipDynamicPlanStoreError):
        _ = store.latest()


def test_store_replays_all_rows_before_returning_latest(tmp_path: Path) -> None:
    first_state = advance_subscription_policy_state(None, _decision(NOW))
    store = AlpacaSipDynamicPlanStore(tmp_path / "dynamic-plans.sqlite3")
    _ = store.roll(first_state)
    next_session = NOW + dt.timedelta(days=3)
    next_decision = build_subscription_policy_decision(
        BroadScannerSnapshot(_identity(), next_session - dt.timedelta(seconds=10), _candidates()),
        evaluated_at=next_session,
        active=first_state.active,
        cooldowns=first_state.cooldowns,
        config=_decision(NOW).config,
    )
    _ = store.roll(advance_subscription_policy_state(first_state, next_decision))
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER dynamic_plan_no_update")
        connection.execute("UPDATE dynamic_plan SET payload_json=X'7B7D' WHERE generation=1")
        connection.execute(
            "CREATE TRIGGER dynamic_plan_no_update BEFORE UPDATE ON dynamic_plan "
            "BEGIN SELECT RAISE(ABORT, 'dynamic plan is append-only'); END"
        )
        connection.commit()

    with pytest.raises(AlpacaSipDynamicPlanStoreError):
        _ = store.latest()


def test_store_rejects_public_or_symlinked_database(tmp_path: Path) -> None:
    target = AlpacaSipDynamicPlanStore(tmp_path / "target.sqlite3")
    _ = target.roll(advance_subscription_policy_state(None, _decision(NOW)))
    target.path.chmod(0o640)

    with pytest.raises(AlpacaSipDynamicPlanStoreError):
        _ = target.latest()

    link = tmp_path / "linked.sqlite3"
    link.symlink_to(target.path)
    with pytest.raises(AlpacaSipDynamicPlanStoreError):
        _ = AlpacaSipDynamicPlanStore(link).latest()
