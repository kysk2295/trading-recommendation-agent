from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trading_agent.research_agent_service_config import ResearchAgentServiceConfig


@dataclass(frozen=True, slots=True)
class KrAutonomousOperatorPaths:
    task_database: Path
    memory_database: Path
    social_signal_database: Path
    trade_database: Path
    position_database: Path
    market_receipt_root: Path


def kr_autonomous_operator_paths(
    config: ResearchAgentServiceConfig,
) -> KrAutonomousOperatorPaths | None:
    if config.schema_version != 4:
        return None
    social = config.kr_social_signal_database
    market = config.kr_market_receipt_root
    if social is None or market is None:
        raise AssertionError("validated schema v4 requires KR operator paths")
    supervisor = config.output_root / "autonomous-supervisor"
    kr_root = supervisor / "kr-v1"
    return KrAutonomousOperatorPaths(
        task_database=supervisor / "tasks.sqlite3",
        memory_database=supervisor / "memory.sqlite3",
        social_signal_database=social,
        trade_database=kr_root / "kr-autonomous-trades.sqlite3",
        position_database=kr_root / "kr-virtual-positions.sqlite3",
        market_receipt_root=market,
    )


__all__ = ("KrAutonomousOperatorPaths", "kr_autonomous_operator_paths")
