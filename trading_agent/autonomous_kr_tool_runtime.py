from __future__ import annotations

import datetime as dt
import functools
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import override

from trading_agent.autonomous_reasoning import AutonomousToolArguments
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_tool_runtime import AutonomousToolBinding, AutonomousToolExecutionContext


@dataclass(frozen=True, slots=True)
class KrVirtualStartupReconciliation:
    open_position_count: int
    appended_event_count: int
    terminal_position_count: int


class InvalidKrVirtualStartupReconciliationError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR virtual startup reconciliation failed"


@dataclass(frozen=True, slots=True)
class KrAutonomousToolServices:
    browser_evidence_database: Path
    social_signal_database: Path
    task_database: Path
    service_config_json: str
    trade_database: Path | None = None
    pending_plan_database: Path | None = None
    position_database: Path | None = None
    startup_at: dt.datetime | None = None
    startup_reconciliation: KrVirtualStartupReconciliation = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "browser_evidence_database", self.browser_evidence_database.absolute())
        object.__setattr__(self, "social_signal_database", self.social_signal_database.absolute())
        object.__setattr__(self, "task_database", self.task_database.absolute())
        trade = self.trade_database or self.task_database.with_name("kr-autonomous-trades.sqlite3")
        pending = self.pending_plan_database or self.task_database.with_name("kr-autonomous-pending-plans.sqlite3")
        object.__setattr__(self, "trade_database", trade.absolute())
        object.__setattr__(self, "pending_plan_database", pending.absolute())
        positions = self.position_database or self.task_database.with_name("kr-virtual-positions.sqlite3")
        object.__setattr__(self, "position_database", positions.absolute())
        now = self.startup_at or dt.datetime.now(dt.UTC)
        object.__setattr__(self, "startup_reconciliation", reconcile_open_kr_virtual_positions(self, now))


def kr_tool_bindings(services: KrAutonomousToolServices) -> tuple[AutonomousToolBinding, ...]:
    from trading_agent.autonomous_kr_tools import (
        corroborate_tool,
        critic_tool,
        execute_tool,
        normalize_tool,
        plan_tool,
        reconcile_tool,
    )

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
        _binding(
            "kr.virtual.execute",
            frozenset({AutonomousAgentRole.TRADING}),
            frozenset({"recommendation_id"}),
            execute_tool,
            {
                "position_database": str(services.position_database),
                "task_database": str(services.task_database),
                "trade_database": str(services.trade_database),
            },
        ),
        _binding(
            "kr.position.reconcile",
            frozenset({AutonomousAgentRole.POSITION}),
            frozenset({"position_id"}),
            reconcile_tool,
            {
                "position_database": str(services.position_database),
                "task_database": str(services.task_database),
                "trade_database": str(services.trade_database),
            },
        ),
    )


def normalize_tool_impl(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    browser_evidence_database: str,
    social_signal_database: str,
    task_database: str,
    normalized_at: dt.datetime,
) -> str:
    from trading_agent._autonomous_kr_tool_support import (
        canonical,
        deny,
        exact_arguments,
        trusted_task,
    )
    from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore
    from trading_agent.kr_social_signal_models import KrSocialSignalRequest, normalize_kr_social_signal
    from trading_agent.kr_social_signal_store import KrSocialSignalStore

    values = exact_arguments(args, {"claim_summary", "evidence_ids_json", "symbol", "theme"})
    task = trusted_task(context, task_database)
    raw_evidence_ids = values["evidence_ids_json"]
    try:
        decoded = json.loads(raw_evidence_ids)
        encoded = json.dumps(decoded, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        evidence_ids = tuple(decoded)
    except (TypeError, ValueError):
        deny("kr_tool_evidence_ids_invalid")
    if (
        raw_evidence_ids != encoded
        or evidence_ids != tuple(sorted(set(evidence_ids)))
        or not all(isinstance(item, str) and len(item) == 64 for item in evidence_ids)
    ):
        deny("kr_tool_evidence_ids_invalid")
    if task.root_source_evidence_id not in evidence_ids:
        deny("kr_tool_root_evidence_denied")
    records = tuple(BrowserSocialEvidenceStore(Path(browser_evidence_database)).get(item) for item in evidence_ids)
    if any(item is None for item in records):
        deny("kr_tool_evidence_missing")
    signal = normalize_kr_social_signal(
        KrSocialSignalRequest(
            task_id=task.task_id,
            symbol=values["symbol"],
            theme=values["theme"],
            claim_summary=values["claim_summary"],
            evidence_ids=evidence_ids,
            normalized_at=normalized_at,
        ),
        tuple(item for item in records if item is not None),
    )
    KrSocialSignalStore(Path(social_signal_database)).append(signal)
    return canonical(
        {
            "signal_id": signal.signal_id,
            "status": "ok",
            "symbol": signal.symbol,
            "verification_state": signal.verification_state.value,
        }
    )


def reconcile_open_kr_virtual_positions(
    services: KrAutonomousToolServices,
    now: dt.datetime,
) -> KrVirtualStartupReconciliation:
    from trading_agent._autonomous_kr_tool_support import observed_completed_bars
    from trading_agent.autonomous_task_store import AutonomousTaskStore
    from trading_agent.kr_autonomous_trade_models import KrTradeRecommendation
    from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
    from trading_agent.kr_virtual_position_engine import advance_kr_virtual_position
    from trading_agent.kr_virtual_position_models import validate_virtual_position_lineage
    from trading_agent.kr_virtual_position_store import KrVirtualPositionStore

    if services.position_database is None or services.trade_database is None:
        raise InvalidKrVirtualStartupReconciliationError
    positions = KrVirtualPositionStore(services.position_database)
    open_positions = positions.open_positions()
    appended = 0
    terminal = 0
    tasks = AutonomousTaskStore(services.task_database).reader()
    trades = KrAutonomousTradeStore(services.trade_database)
    for previous in open_positions:
        task = tasks.task(previous.task_id)
        recommendation = trades.event(previous.recommendation_id)
        if (
            task is None
            or task.market_scope != "kr_equities"
            or not isinstance(recommendation, KrTradeRecommendation)
            or recommendation.task_id != task.task_id
        ):
            raise InvalidKrVirtualStartupReconciliationError
        validate_virtual_position_lineage(recommendation, previous)
        bars = observed_completed_bars(str(services.task_database), task, recommendation.symbol)
        events = advance_kr_virtual_position(recommendation, previous, bars, now)
        for event in events:
            appended += int(positions.append(event))
        terminal += int(bool(events) and events[-1].terminal)
    return KrVirtualStartupReconciliation(len(open_positions), appended, terminal)


def _binding(
    name: str,
    roles: frozenset[AutonomousAgentRole],
    arguments: frozenset[str],
    callback: Callable[..., str],
    bound: dict[str, str],
) -> AutonomousToolBinding:
    return AutonomousToolBinding(name, roles, arguments, functools.partial(callback, **bound), ())
