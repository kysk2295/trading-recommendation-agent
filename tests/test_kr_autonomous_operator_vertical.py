from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_kr_autonomous_outcome_learning import _collision_bar, _paths, _signal_for_task
from tests.test_kr_autonomous_trade_planner import _request
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.dashboard_kr_autonomous_operator import project_kr_autonomous_operator
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_autonomous_hermes import project_kr_autonomous_state
from trading_agent.kr_autonomous_outcome_learning import observe_kr_autonomous_outcomes
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousRejected,
    KrCriticReason,
    KrTradeRecommendation,
    event_id,
)
from trading_agent.kr_autonomous_trade_planner import plan_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_virtual_position_engine import advance_kr_virtual_position, arm_kr_virtual_position
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 26, 13, 4, 4, tzinfo=KST)


def test_recommendation_terminal_learning_delivery_dashboard_and_restart_are_one_lineage(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    request = _request()
    recommendation = plan_kr_autonomous_trade(request)
    assert isinstance(recommendation, KrTradeRecommendation)
    signals = KrSocialSignalStore(paths.social_signal_database)
    trades = KrAutonomousTradeStore(paths.trade_database)
    positions = KrVirtualPositionStore(paths.position_database)
    assert signals.append(request.social_signal)
    assert trades.append(recommendation)
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    stopped = advance_kr_virtual_position(
        recommendation,
        armed,
        (_collision_bar(recommendation),),
        NOW + dt.timedelta(minutes=2, seconds=1),
    )[0]
    assert positions.append(armed)
    assert positions.append(stopped)
    previous = recommendation.event_id
    for marker in range(3):
        task_id = f"{marker + 1:064x}"
        signal = _signal_for_task(request.social_signal, task_id)
        assert signals.append(signal)
        draft = KrAutonomousRejected.model_construct(
            event_id="",
            plan_id=f"{marker + 11:064x}",
            previous_event_id=previous,
            timestamp=NOW + dt.timedelta(seconds=marker + 1),
            task_id=task_id,
            thesis_id=f"{marker + 21:064x}",
            symbol=signal.symbol,
            theme=signal.theme,
            reason_codes=(KrCriticReason.CLUSTER_COUNT,),
            critic_verdict_id=f"{marker + 31:064x}",
            next_wake_at=NOW + dt.timedelta(minutes=1, seconds=marker + 1),
        )
        rejected = KrAutonomousRejected.model_validate(
            draft.model_copy(update={"event_id": event_id(draft)}).model_dump(mode="python")
        )
        assert trades.append(rejected)
        previous = rejected.event_id
    observed_at = NOW + dt.timedelta(minutes=2, seconds=1)

    learning = observe_kr_autonomous_outcomes(paths, now=observed_at)
    memory = AutonomousMemoryStore(paths.memory_database).reader()
    outcome_record = memory.latest(learning.memory_keys[0])
    bundle_record = memory.latest(learning.bundle_keys[0])
    assert outcome_record is not None and bundle_record is not None
    hermes_path = tmp_path / "hermes.sqlite3"
    with HermesDeliveryStore(hermes_path).writer() as writer:
        delivered = project_kr_autonomous_state(paths, writer, projected_source_ids=frozenset())
    dashboard = project_kr_autonomous_operator(paths, now=observed_at)

    delivered_sources = {event.source_event_id for event in HermesDeliveryReader(hermes_path).events()}
    assert learning.inserted_memories == 4
    assert learning.inserted_bundles == 1
    assert delivered.inserted == 11
    assert {recommendation.event_id, stopped.event_id, str(outcome_record.memory_id), str(bundle_record.memory_id)} <= (
        delivered_sources
    )
    assert f"kr-decision-{recommendation.event_id[:24]}" in {item.item_id for item in dashboard.markets.items}
    assert f"kr-position-{stopped.position_id[:24]}" in {item.item_id for item in dashboard.paper.items}
    assert f"kr-outcome-{outcome_record.memory_id[:24]}" in {item.item_id for item in dashboard.research.items}
    assert f"kr-loop-{bundle_record.memory_id[:24]}" in {item.item_id for item in dashboard.research.items}

    replay_learning = observe_kr_autonomous_outcomes(paths, now=observed_at)
    restarted_ids = frozenset(event.source_event_id for event in HermesDeliveryReader(hermes_path).events())
    with HermesDeliveryStore(hermes_path).writer() as writer:
        replay_delivery = project_kr_autonomous_state(paths, writer, projected_source_ids=restarted_ids)
    replay_dashboard = project_kr_autonomous_operator(paths, now=observed_at)
    assert replay_learning.inserted_memories == replay_learning.inserted_bundles == 0
    assert replay_delivery.inserted == replay_delivery.examined == 0
    assert replay_dashboard == dashboard
    assert all("real" not in (item.value or "").lower() for item in dashboard.paper.items)
