from __future__ import annotations

from pathlib import Path

from tests.research_agent_browser_service_fixtures import browser_service_config
from tests.test_autonomous_supervisor_service import _defer_client
from tests.test_autonomous_task_models import task_fixture
from tests.test_kr_virtual_position_store import _recommendation_for_task
from trading_agent.autonomous_browser_tools import BrowserToolServices
from trading_agent.autonomous_supervisor_service import autonomous_supervisor_paths, build_autonomous_supervisor
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_virtual_position_engine import arm_kr_virtual_position
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore
from trading_agent.local_browser_gateway_config import load_local_browser_gateway_config
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig


def test_v4_production_builder_reconciles_before_return_and_enables_kr_tools(tmp_path: Path) -> None:
    # Given: schema v4 and one durable open KR virtual position in its production stores.
    config = _v4_config(tmp_path)
    paths = autonomous_supervisor_paths(config)
    task = task_fixture()
    recommendation = _recommendation_for_task(task.task_id)
    kr_root = paths.task_database.parent / "kr-v1"
    with AutonomousTaskStore(paths.task_database).writer() as writer:
        assert writer.create_task(task)
    assert KrAutonomousTradeStore(kr_root / "kr-autonomous-trades.sqlite3").append(recommendation)
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    positions = KrVirtualPositionStore(kr_root / "kr-virtual-positions.sqlite3")
    assert positions.append(armed)

    # When: the production supervisor is constructed at the recommendation expiry boundary.
    adapter = build_autonomous_supervisor(
        config,
        client=_defer_client(),
        clock=lambda: recommendation.valid_until,
        browser=_browser_services(config),
    )
    try:
        events = positions.events(armed.position_id)

        # Then: startup reconciliation is already durable before any runtime work is accepted.
        assert tuple(event.state.value for event in events) == ("ARMED", "EXPIRED")
        assert "kr.virtual.execute" in adapter.runtime.tools.allowed_tool_names
        assert "kr.position.reconcile" in adapter.runtime.tools.allowed_tool_names
    finally:
        adapter.close()


def test_v3_production_builder_preserves_browser_tools_without_kr_bindings(tmp_path: Path) -> None:
    # Given: the existing schema-v3 browser-bound service configuration.
    config = browser_service_config(tmp_path)

    # When: the production supervisor is constructed through the same builder seam.
    adapter = build_autonomous_supervisor(config, client=_defer_client(), browser=_browser_services(config))
    try:
        names = adapter.runtime.tools.allowed_tool_names

        # Then: browser authority remains enabled and no KR recommendation authority is added.
        assert "browser.search" in names
        assert not any(name.startswith("kr.") or name == "social.signal.normalize" for name in names)
    finally:
        adapter.close()


def _v4_config(tmp_path: Path) -> ResearchAgentServiceConfig:
    source = browser_service_config(tmp_path)
    return ResearchAgentServiceConfig.model_validate(
        source.model_dump(mode="python")
        | {
            "schema_version": 4,
            "kr_market_receipt_root": tmp_path / "market-receipts",
            "kr_social_signal_database": tmp_path / "social-signals.sqlite3",
        }
    )


def _browser_services(config: ResearchAgentServiceConfig) -> BrowserToolServices:
    assert config.browser_gateway_config is not None
    gateway = load_local_browser_gateway_config(config.browser_gateway_config)
    evidence = config.output_root / "autonomous-supervisor" / "browser-social-evidence.sqlite3"
    return BrowserToolServices(gateway.socket_path, evidence)
