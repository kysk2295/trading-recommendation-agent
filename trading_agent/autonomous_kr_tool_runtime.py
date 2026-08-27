from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_tool_runtime import AutonomousToolBinding


@dataclass(frozen=True, slots=True)
class KrAutonomousToolServices:
    browser_evidence_database: Path
    social_signal_database: Path
    task_database: Path
    service_config_json: str
    trade_database: Path | None = None
    pending_plan_database: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "browser_evidence_database", self.browser_evidence_database.absolute())
        object.__setattr__(self, "social_signal_database", self.social_signal_database.absolute())
        object.__setattr__(self, "task_database", self.task_database.absolute())
        trade = self.trade_database or self.task_database.with_name("kr-autonomous-trades.sqlite3")
        pending = self.pending_plan_database or self.task_database.with_name("kr-autonomous-pending-plans.sqlite3")
        object.__setattr__(self, "trade_database", trade.absolute())
        object.__setattr__(self, "pending_plan_database", pending.absolute())


def kr_tool_bindings(services: KrAutonomousToolServices) -> tuple[AutonomousToolBinding, ...]:
    from trading_agent.autonomous_kr_tools import corroborate_tool, critic_tool, normalize_tool, plan_tool

    bound = {
        "browser_evidence_database": str(services.browser_evidence_database),
        "social_signal_database": str(services.social_signal_database),
        "task_database": str(services.task_database),
        "service_config_json": services.service_config_json,
        "trade_database": str(services.trade_database),
        "pending_plan_database": str(services.pending_plan_database),
    }
    return (
        _binding(
            "social.signal.normalize",
            frozenset({AutonomousAgentRole.MARKET_OBSERVER, AutonomousAgentRole.RESEARCH}),
            frozenset({"claim_summary", "evidence_ids_json", "symbol", "theme"}),
            normalize_tool,
            bound,
        ),
        _binding(
            "kr.market.corroborate",
            frozenset({AutonomousAgentRole.OPPORTUNITY, AutonomousAgentRole.RESEARCH}),
            frozenset({"signal_id", "symbol"}),
            corroborate_tool,
            bound,
        ),
        _binding(
            "kr.trade.plan", frozenset({AutonomousAgentRole.TRADING}), frozenset({"thesis_json"}), plan_tool, bound
        ),
        _binding("critic.request", frozenset({AutonomousAgentRole.CRITIC}), frozenset({"plan_id"}), critic_tool, bound),
    )


def _binding(
    name: str,
    roles: frozenset[AutonomousAgentRole],
    arguments: frozenset[str],
    callback: Callable[..., str],
    bound: dict[str, str],
) -> AutonomousToolBinding:
    return AutonomousToolBinding(name, roles, arguments, functools.partial(callback, **bound), ())
