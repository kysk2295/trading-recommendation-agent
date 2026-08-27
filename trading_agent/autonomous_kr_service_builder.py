from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import assert_never

from trading_agent.autonomous_kr_tool_runtime import KrAutonomousToolServices
from trading_agent.research_agent_service_config import (
    InvalidResearchAgentServiceConfigError,
    ResearchAgentServiceConfig,
)


def kr_tool_services_for_config(
    config: ResearchAgentServiceConfig,
    task_database: Path,
    clock: Callable[[], dt.datetime],
) -> KrAutonomousToolServices | None:
    match config.schema_version:
        case 2 | 3:
            return None
        case 4:
            signal_database = config.kr_social_signal_database
            if signal_database is None:
                raise InvalidResearchAgentServiceConfigError(reason="service_kr_binding_invalid")
            supervisor_root = task_database.parent
            kr_root = supervisor_root / "kr-v1"
            config_json = json.dumps(
                config.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            return KrAutonomousToolServices(
                browser_evidence_database=supervisor_root / "browser-social-evidence.sqlite3",
                social_signal_database=signal_database,
                task_database=task_database,
                service_config_json=config_json,
                trade_database=kr_root / "kr-autonomous-trades.sqlite3",
                pending_plan_database=kr_root / "kr-autonomous-pending-plans.sqlite3",
                position_database=kr_root / "kr-virtual-positions.sqlite3",
                startup_at=clock(),
            )
        case unreachable:
            assert_never(unreachable)
