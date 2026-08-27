from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_kr_autonomous_outcome_learning import _paths
from tests.test_kr_autonomous_trade_planner import _request
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_autonomous_hermes import project_kr_autonomous_state
from trading_agent.kr_autonomous_outcome_learning import observe_kr_autonomous_outcomes
from trading_agent.kr_autonomous_trade_models import KrTradeRecommendation
from trading_agent.kr_autonomous_trade_planner import plan_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_virtual_position_engine import arm_kr_virtual_position
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 26, 13, 4, 4, tzinfo=KST)


def test_kr_state_changes_project_safe_virtual_lineage_once(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    request = _request()
    recommendation = plan_kr_autonomous_trade(request)
    assert isinstance(recommendation, KrTradeRecommendation)
    assert KrSocialSignalStore(paths.social_signal_database).append(request.social_signal)
    assert KrAutonomousTradeStore(paths.trade_database).append(recommendation)
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    assert KrVirtualPositionStore(paths.position_database).append(armed)
    learning = observe_kr_autonomous_outcomes(paths, now=NOW)
    assert learning.inserted_memories == 1
    database = tmp_path / "hermes.sqlite3"

    with HermesDeliveryStore(database).writer() as writer:
        first = project_kr_autonomous_state(paths, writer, projected_source_ids=frozenset())
    projected_ids = frozenset(event.source_event_id for event in HermesDeliveryReader(database).events())
    with HermesDeliveryStore(database).writer() as writer:
        replay = project_kr_autonomous_state(paths, writer, projected_source_ids=projected_ids)

    events = HermesDeliveryReader(database).events()
    recommendation_event = next(item for item in events if item.source_event_id == recommendation.event_id)
    position_event = next(item for item in events if item.source_event_id == armed.event_id)
    memory_event = next(
        item for item in events if item.source_event_id not in {recommendation.event_id, armed.event_id}
    )
    assert first.examined == first.inserted == 3
    assert replay.examined == replay.inserted == replay.replayed == 0
    assert recommendation_event.kind is HermesDeliveryKind.ACTIONABLE
    assert recommendation_event.status == "virtual_recommendation"
    assert all(
        value in recommendation_event.rendered_text
        for value in (
            "가상",
            recommendation.symbol,
            str(recommendation.entry),
            str(recommendation.stop),
            str(recommendation.targets[0]),
            str(recommendation.targets[1]),
            recommendation.rationale,
            recommendation.valid_until.isoformat(),
        )
    )
    assert position_event.root_delivery_id == recommendation_event.delivery_id
    assert position_event.status == "virtual_armed"
    assert "가상" in position_event.rendered_text
    assert memory_event.kind is HermesDeliveryKind.RESEARCH
    assert memory_event.root_delivery_id == recommendation_event.delivery_id
    assert "수익" not in memory_event.rendered_text
    assert all("authority" not in item.rendered_text.lower() for item in events)
