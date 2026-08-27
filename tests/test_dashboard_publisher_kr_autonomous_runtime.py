from __future__ import annotations

from pathlib import Path

import run_dashboard_publisher
from tests.test_research_agent_service_cli import _config
from trading_agent.dashboard_publisher_events import watch_roots
from trading_agent.kr_autonomous_operator_paths import kr_autonomous_operator_paths
from trading_agent.research_agent_service_config import (
    ResearchAgentServiceConfig,
    write_research_agent_service_config,
)


def test_schema_v4_dashboard_binding_is_loaded_once_and_watched(tmp_path: Path) -> None:
    base = _config(tmp_path)
    config = ResearchAgentServiceConfig.model_validate(
        base.model_dump(mode="python")
        | {
            "schema_version": 4,
            "browser_gateway_config": (tmp_path / "browser.json").absolute(),
            "kr_market_receipt_root": (tmp_path / "market-receipts").absolute(),
            "kr_social_signal_database": (tmp_path / "signals.sqlite3").absolute(),
        }
    )
    config_path = (tmp_path / "private" / "research.json").absolute()
    assert write_research_agent_service_config(config_path, config)
    binding = run_dashboard_publisher._research_binding(config_path)
    expected = kr_autonomous_operator_paths(config)
    assert expected is not None
    expected.task_database.parent.mkdir(parents=True)

    roots = watch_roots(tmp_path / "outputs", kr_operator_paths=binding.operator_paths)

    assert binding.cycle_database == config.cycle_database
    assert binding.operator_paths == expected
    assert expected.task_database.parent.resolve() in roots
