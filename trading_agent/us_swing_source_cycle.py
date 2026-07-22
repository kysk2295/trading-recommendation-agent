from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, assert_never

from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT
from trading_agent.swing_source_incident_delivery import project_swing_source_incident
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_swing_operating_models import (
    InvalidSwingOperatingRequestError,
    SwingOperatingConfig,
    SwingOperatingRequest,
    SwingScanCompleted,
    SwingScanFailed,
)

_SWING_LANE: Final = StrategyLaneRef(
    market_id=MarketId.US_EQUITIES,
    agent_family=AgentFamily.SWING_TRADING,
    strategy_id=SWING_RESEARCH_CONTRACT.strategy_id,
)


@dataclass(frozen=True, slots=True)
class SwingSourceCycleResult:
    operation_request: SwingOperatingRequest
    scanner_executed: bool
    incidents: int


def run_post_close_swing_source_cycle(
    request: SwingOperatingRequest,
    config: SwingOperatingConfig,
) -> SwingSourceCycleResult:
    session_date = request.now.astimezone(NEW_YORK).date()
    if _has_completed_source_cycle(config.delivery_store, session_date):
        return SwingSourceCycleResult(request, False, 0)
    outcome = config.scanner.run(session_date)
    match outcome:
        case SwingScanCompleted(completed_at=completed_at):
            operation_request = SwingOperatingRequest(
                _validated_scan_time(completed_at, request, session_date),
                request.runtime_code_version,
            )
            incidents = 0
        case SwingScanFailed(failed_at=failed_at):
            operation_request = SwingOperatingRequest(
                _validated_scan_time(failed_at, request, session_date),
                request.runtime_code_version,
            )
            incidents = project_swing_source_incident(
                session_date,
                outcome,
                config.delivery_store,
            ).examined
        case unreachable:
            assert_never(unreachable)
    return SwingSourceCycleResult(operation_request, True, incidents)


def _has_completed_source_cycle(store: HermesDeliveryStore, session_date: dt.date) -> bool:
    return any(
        event.root_delivery_id == event.delivery_id
        and event.kind in {HermesDeliveryKind.WATCH, HermesDeliveryKind.NO_RECOMMENDATION}
        and event.occurred_at.astimezone(NEW_YORK).date() == session_date
        and event.market_id == _SWING_LANE.market_id.value
        and event.agent_family == _SWING_LANE.agent_family.value
        and event.lane_id == _SWING_LANE.canonical_id
        and event.strategy_version == SWING_RESEARCH_CONTRACT.strategy_version
        for event in store.events()
    )


def _validated_scan_time(
    value: dt.datetime,
    request: SwingOperatingRequest,
    session_date: dt.date,
) -> dt.datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value < request.now
        or value.astimezone(NEW_YORK).date() != session_date
    ):
        raise InvalidSwingOperatingRequestError
    return value


__all__ = ("SwingSourceCycleResult", "run_post_close_swing_source_cycle")
