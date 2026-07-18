from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_subscription_models import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
)
from trading_agent.us_subscription_policy_state import (
    SubscriptionPolicyStateError,
    advance_subscription_policy_state,
)
from trading_agent.us_subscription_policy_state_store import SubscriptionPolicyStateStore

NOW = dt.datetime(2026, 7, 17, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
CONFIG = SubscriptionPolicyConfig(
    1,
    dt.timedelta(seconds=30),
    dt.timedelta(minutes=2),
    dt.timedelta(minutes=5),
)


def test_restart_preserves_original_subscription_time_and_exact_retry(tmp_path: Path) -> None:
    first = _decision(NOW, (_candidate("us-eq-a", "AAA", "10", 1),), None)
    state = advance_subscription_policy_state(None, first)
    store = SubscriptionPolicyStateStore(tmp_path / "policy-state.sqlite3")

    assert store.append(state) is True
    assert store.append(state) is False
    replay = store.latest()
    assert replay == state
    assert replay is not None
    assert replay.active[0].subscribed_at == NOW
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    later = NOW + dt.timedelta(seconds=30)
    restarted = _decision(
        later,
        (
            _candidate("us-eq-b", "BBB", "100", 1),
            _candidate("us-eq-a", "AAA", "1", 2),
        ),
        replay,
    )
    assert tuple(item.instrument_id for item in restarted.desired) == ("us-eq-a",)
    next_state = advance_subscription_policy_state(replay, restarted)
    assert next_state.active[0].subscribed_at == NOW


def test_eviction_cooldown_survives_restart_and_blocks_reentry(tmp_path: Path) -> None:
    first = advance_subscription_policy_state(
        None,
        _decision(NOW, (_candidate("us-eq-a", "AAA", "10", 1),), None),
    )
    eviction_at = NOW + dt.timedelta(minutes=3)
    eviction = _decision(
        eviction_at,
        (
            _candidate("us-eq-b", "BBB", "100", 1),
            _candidate("us-eq-a", "AAA", "1", 2),
        ),
        first,
    )
    second = advance_subscription_policy_state(first, eviction)
    store = SubscriptionPolicyStateStore(tmp_path / "policy-state.sqlite3")
    assert store.append(first)
    assert store.append(second)
    replay = store.latest()
    assert replay == second
    assert replay is not None
    assert replay.cooldowns[0].instrument_id == "us-eq-a"

    retry_at = eviction_at + dt.timedelta(minutes=1)
    blocked = _decision(
        retry_at,
        (
            _candidate("us-eq-a", "AAA", "100", 1),
            _candidate("us-eq-b", "BBB", "1", 2),
        ),
        replay,
    )
    assert tuple(item.instrument_id for item in blocked.desired) == ("us-eq-b",)


def test_tampered_state_payload_fails_replay(tmp_path: Path) -> None:
    store = SubscriptionPolicyStateStore(tmp_path / "policy-state.sqlite3")
    state = advance_subscription_policy_state(
        None,
        _decision(NOW, (_candidate("us-eq-a", "AAA", "10", 1),), None),
    )
    assert store.append(state)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER subscription_policy_state_no_update")
        connection.execute("UPDATE subscription_policy_state SET payload_json=X'7B7D'")
        connection.commit()

    with pytest.raises(SubscriptionPolicyStateError, match="invalid"):
        _ = store.latest()


def test_symlinked_or_public_state_file_fails_closed(tmp_path: Path) -> None:
    state = advance_subscription_policy_state(
        None,
        _decision(NOW, (_candidate("us-eq-a", "AAA", "10", 1),), None),
    )
    target = SubscriptionPolicyStateStore(tmp_path / "target.sqlite3")
    assert target.append(state)
    target.path.chmod(0o640)
    with pytest.raises(SubscriptionPolicyStateError, match="invalid"):
        _ = target.latest()

    link = tmp_path / "linked.sqlite3"
    link.symlink_to(target.path)
    with pytest.raises(SubscriptionPolicyStateError, match="invalid"):
        _ = SubscriptionPolicyStateStore(link).latest()


def _decision(now: dt.datetime, candidates, state):
    return build_subscription_policy_decision(
        BroadScannerSnapshot(_identity(), now - dt.timedelta(seconds=1), candidates),
        evaluated_at=now,
        active=() if state is None else state.active,
        cooldowns=() if state is None else state.cooldowns,
        config=CONFIG,
    )


def _candidate(instrument_id: str, symbol: str, score: str, rank: int) -> BroadScannerCandidate:
    return BroadScannerCandidate(instrument_id, symbol, Decimal(score), rank)


def _identity() -> ResearchInputIdentity:
    return ResearchInputIdentity.from_verified_replay(
        "us_equities.policy_state.fixture",
        CanonicalDatasetReplay(
            "ds_policy_state",
            1,
            "a" * 64,
            "c" * 64,
            "raw_policy_state",
            "b" * 64,
        ),
    )
