from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from tests.test_kr_autonomous_trade_planner import _request
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousNoTrade,
    KrCriticReason,
    KrOpenVirtualExposure,
    KrTradeRecommendation,
    event_id,
    verdict_id,
)
from trading_agent.kr_autonomous_trade_planner import plan_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import InvalidKrAutonomousTradeStoreError, KrAutonomousTradeStore


def test_store_appends_replays_and_reads_immutable_history(tmp_path: Path) -> None:
    # Given: one deterministic recommendation event.
    event = plan_kr_autonomous_trade(_request())
    store = KrAutonomousTradeStore(tmp_path / "private" / "trade.sqlite3")

    # When: it is appended and exactly replayed.
    assert store.append(event) is True
    assert store.append(event) is False

    # Then: content and chain order replay exactly from private storage.
    assert store.events() == (event,)
    assert store.event(event.event_id) == event
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_store_rejects_tampered_embedded_critic_lineage(tmp_path: Path) -> None:
    # Given: a recommendation whose embedded Critic points at another thesis.
    event = plan_kr_autonomous_trade(_request())
    assert isinstance(event, KrTradeRecommendation)
    forged_critic = event.critic_verdict.model_copy(update={"thesis_id": "f" * 64})
    forged = event.model_copy(update={"critic_verdict": forged_critic})

    # When/Then: neither append nor direct lookup can admit divergent lineage.
    store = KrAutonomousTradeStore(tmp_path / "trade.sqlite3")
    with pytest.raises(InvalidKrAutonomousTradeStoreError):
        _ = store.append(forged)
    assert store.event(event.event_id) is None


def test_store_rejects_readdressed_proposal_field_tamper(tmp_path: Path) -> None:
    # Given: a recommendation changes its entry and recomputes only the event identity.
    event = plan_kr_autonomous_trade(_request())
    assert isinstance(event, KrTradeRecommendation)
    forged = event.model_copy(update={"entry": event.entry + 1})
    forged = forged.model_copy(update={"event_id": event_id(forged)})

    # When/Then: proposal content identity still rejects the changed level.
    with pytest.raises(InvalidKrAutonomousTradeStoreError):
        _ = KrAutonomousTradeStore(tmp_path / "trade.sqlite3").append(forged)


def test_store_rejects_readdressed_approved_verdict_with_rejection_reason(tmp_path: Path) -> None:
    # Given: an embedded approved verdict is readdressed with a rejection reason.
    event = plan_kr_autonomous_trade(_request())
    assert isinstance(event, KrTradeRecommendation)
    critic = event.critic_verdict.model_copy(update={"reason_codes": (KrCriticReason.TASK_LINEAGE,)})
    critic = critic.model_copy(update={"verdict_id": verdict_id(critic)})
    forged = event.model_copy(update={"critic_verdict": critic, "critic_verdict_id": critic.verdict_id})
    forged = forged.model_copy(update={"event_id": event_id(forged)})

    # When/Then: approval semantics cannot be forged by recomputing content IDs.
    with pytest.raises(InvalidKrAutonomousTradeStoreError):
        _ = KrAutonomousTradeStore(tmp_path / "trade.sqlite3").append(forged)


def test_store_rejects_broken_chain_and_divergent_identity(tmp_path: Path) -> None:
    # Given: a store with a first recommendation event.
    first = plan_kr_autonomous_trade(_request())
    assert isinstance(first, KrTradeRecommendation)
    store = KrAutonomousTradeStore(tmp_path / "trade.sqlite3")
    assert store.append(first)
    broken = first.model_copy(update={"previous_event_id": "f" * 64, "event_id": "e" * 64})
    divergent = first.model_copy(update={"quantity": first.quantity - 1})

    # When/Then: invalid identity and chain attempts fail closed.
    with pytest.raises(InvalidKrAutonomousTradeStoreError):
        _ = store.append(broken)
    with pytest.raises(InvalidKrAutonomousTradeStoreError):
        _ = store.append(divergent)


def test_store_persists_no_trade_without_price_fields(tmp_path: Path) -> None:
    # Given: a duplicate exposure produces an explicit no-trade event.
    request = _request()
    first = plan_kr_autonomous_trade(request)
    store = KrAutonomousTradeStore(tmp_path / "trade.sqlite3")
    assert store.append(first)
    duplicate = request.model_copy(
        update={
            "previous_event_id": first.event_id,
            "open_exposures": (KrOpenVirtualExposure(symbol=request.thesis.symbol, theme=request.thesis.theme),),
        }
    )

    # When: the chained no-trade outcome is appended.
    no_trade = plan_kr_autonomous_trade(duplicate)
    assert isinstance(no_trade, KrAutonomousNoTrade)
    assert store.append(no_trade)

    # Then: replay preserves the outcome and schema omits price/quantity fields.
    assert store.events()[-1] == no_trade
    assert "entry" not in no_trade.model_dump() and "quantity" not in no_trade.model_dump()


def test_store_detects_database_tamper_and_append_only_triggers(tmp_path: Path) -> None:
    # Given: one persisted event in the private ledger.
    path = tmp_path / "trade.sqlite3"
    store = KrAutonomousTradeStore(path)
    assert store.append(plan_kr_autonomous_trade(_request()))

    # When: direct mutation is attempted and then the payload hash is corrupted.
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM kr_autonomous_trade_events")
        connection.execute("DROP TRIGGER kr_autonomous_trade_events_no_update")
        connection.execute("UPDATE kr_autonomous_trade_events SET payload_sha256=?", ("0" * 64,))

    # Then: query-only replay rejects the corrupted authority.
    with pytest.raises(InvalidKrAutonomousTradeStoreError):
        _ = store.events()
