from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_kr_autonomous_outcome_learning import _paths
from tests.test_kr_autonomous_trade_planner import _request
from trading_agent.dashboard_kr_autonomous_operator import project_kr_autonomous_operator
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.kr_autonomous_outcome_learning import observe_kr_autonomous_outcomes
from trading_agent.kr_autonomous_trade_models import KrTradeRecommendation
from trading_agent.kr_autonomous_trade_planner import plan_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_virtual_position_engine import arm_kr_virtual_position
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 26, 13, 4, 4, tzinfo=KST)


def test_kr_operator_projects_one_safe_lineage_into_three_workspaces(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    request = _request()
    recommendation = plan_kr_autonomous_trade(request)
    assert isinstance(recommendation, KrTradeRecommendation)
    assert KrSocialSignalStore(paths.social_signal_database).append(request.social_signal)
    assert KrAutonomousTradeStore(paths.trade_database).append(recommendation)
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    assert KrVirtualPositionStore(paths.position_database).append(armed)
    assert observe_kr_autonomous_outcomes(paths, now=NOW).inserted_memories == 1

    projection = project_kr_autonomous_operator(paths, now=NOW)
    snapshot = collect_dashboard_snapshot_v2(tmp_path / "outputs", now=NOW, kr_operator_paths=paths)

    assert projection.markets.total_count == projection.paper.total_count == projection.research.total_count == 1
    market = projection.markets.items[0]
    paper = projection.paper.items[0]
    research = projection.research.items[0]
    assert market.item_id == f"kr-decision-{recommendation.event_id[:24]}"
    assert all(
        value in (market.value or "")
        for value in (
            "virtual",
            f"entry={recommendation.entry}",
            f"stop={recommendation.stop}",
            f"targets={recommendation.targets[0]}/{recommendation.targets[1]}",
            recommendation.verification_state.value,
        )
    )
    assert paper.label.startswith("KR 가상")
    assert "virtual" in (paper.value or "")
    assert "가상 결과" in research.label
    assert any(edge.kind == "executed_as" for edge in projection.edges)
    assert any(edge.kind == "evaluated_in" for edge in projection.edges)
    assert any(node.safe_ref == recommendation.task_id for node in projection.nodes)
    assert market.item_id in {item.item_id for item in snapshot.workspaces.markets.items}
    assert paper.item_id in {item.item_id for item in snapshot.workspaces.paper.items}
    assert research.item_id in {item.item_id for item in snapshot.workspaces.research.items}
    assert snapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_missing_kr_operator_stores_project_empty_without_creating_files(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    projection = project_kr_autonomous_operator(paths, now=NOW)

    assert projection.markets.items == projection.paper.items == projection.research.items == ()
    assert projection.nodes == projection.edges == ()
    assert not paths.trade_database.exists()
    assert not paths.position_database.exists()
    assert not paths.memory_database.exists()
