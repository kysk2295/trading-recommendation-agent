from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.dashboard_outbound_redaction import redact_outbound_text, require_safe_outbound_text
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord, HermesProjectionResult, project_outcomes
from trading_agent.hermes_delivery_store import HermesDeliveryWriter
from trading_agent.strategy_lab_models import (
    STRATEGY_LAB_IDS,
    StrategyLabId,
    StrategyLabProtocol,
    StrategyLabTraceNode,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_NO_PROJECTED_SOURCE_EVENT_IDS: Final[frozenset[str]] = frozenset()
_REPORT_DELAY: Final = dt.timedelta(minutes=15)
_SUMMARY_SOURCE_PREFIX: Final = "strategy-lab-daily-summary"


@dataclass(frozen=True, slots=True)
class InvalidStrategyLabDailyReportError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class StrategyLabDailyState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["completed", "waiting_evidence", "waiting_availability", "blocked"]
    current_cycle: int = Field(ge=0)
    trace_depths: tuple[tuple[StrategyLabId, int], ...]
    evidence_bundle_available: bool

    @model_validator(mode="after")
    def validate_trace_depths(self) -> Self:
        if not self.trace_depths:
            return self
        lab_ids = tuple(lab_id for lab_id, _ in self.trace_depths)
        depths = tuple(depth for _, depth in self.trace_depths)
        if lab_ids != STRATEGY_LAB_IDS or any(depth < 0 for depth in depths):
            raise InvalidStrategyLabDailyReportError("strategy_lab_daily_state_trace_invalid")
        if len(set(depths)) != 1 or depths[0] != self.current_cycle:
            raise InvalidStrategyLabDailyReportError("strategy_lab_daily_state_depth_invalid")
        return self


def project_strategy_lab_daily_report(
    reader: ExperimentLedgerReader,
    writer: HermesDeliveryWriter,
    *,
    now: dt.datetime,
    projected_source_event_ids: frozenset[str] = _NO_PROJECTED_SOURCE_EVENT_IDS,
    runtime_state: StrategyLabDailyState | None = None,
) -> HermesProjectionResult:
    session = _latest_completed_session(now)
    cycle = _complete_cycle(reader)
    if session is None or (cycle is None and runtime_state is None):
        return HermesProjectionResult(examined=0, inserted=0, replayed=0)
    session_date, occurred_at = session
    source_event_id = f"{_SUMMARY_SOURCE_PREFIX}:{session_date.isoformat()}"
    if source_event_id in projected_source_event_ids:
        return HermesProjectionResult(examined=0, inserted=0, replayed=0)
    record = _report_record(source_event_id, occurred_at, cycle, runtime_state)
    return project_outcomes((record,), writer)


def _latest_completed_session(now: dt.datetime) -> tuple[dt.date, dt.datetime] | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidStrategyLabDailyReportError("strategy_lab_daily_report_now_must_be_aware")
    local_now = now.astimezone(NEW_YORK)
    current_bounds = regular_session_bounds(local_now.date())
    if current_bounds is not None and local_now < current_bounds[1] + _REPORT_DELAY:
        return None
    for days_ago in range(14):
        session_date = local_now.date() - dt.timedelta(days=days_ago)
        bounds = regular_session_bounds(session_date)
        if bounds is None:
            continue
        _, close = bounds
        occurred_at = close + _REPORT_DELAY
        if local_now >= occurred_at:
            return session_date, occurred_at
    return None


def _complete_cycle(
    reader: ExperimentLedgerReader,
) -> tuple[tuple[StrategyLabProtocol, StrategyLabTraceNode], ...] | None:
    try:
        protocols_by_id = {protocol.protocol_id: protocol for protocol in reader.strategy_lab_protocols()}
        latest = tuple(reader.strategy_lab_trace(lab_id)[-1:] for lab_id in STRATEGY_LAB_IDS)
    except (
        InvalidExperimentLedgerSourceError,
        UnsupportedExperimentLedgerSchemaError,
        sqlite3.DatabaseError,
    ):
        return None
    if any(not trace for trace in latest):
        return None
    nodes = tuple(trace[0] for trace in latest)
    iterations = {node.body.iteration for node in nodes}
    if len(iterations) != 1:
        return None
    resolved = tuple((protocols_by_id.get(node.body.protocol_id), node) for node in nodes)
    if any(protocol is None for protocol, _ in resolved):
        return None
    return tuple((protocol, node) for protocol, node in resolved if protocol is not None)


def _report_record(
    source_event_id: str,
    occurred_at: dt.datetime,
    cycle: tuple[tuple[StrategyLabProtocol, StrategyLabTraceNode], ...] | None,
    runtime_state: StrategyLabDailyState | None,
) -> HermesProjectionRecord:
    rendered_text = _render_report(source_event_id, cycle, runtime_state)
    return HermesProjectionRecord(
        source_event_id=source_event_id,
        root_source_event_id=None,
        kind=HermesDeliveryKind.DAILY_SUMMARY,
        market_id="us_equities",
        agent_family="strategy_lab",
        lane_id=None,
        strategy_version=None,
        instrument_id=None,
        occurred_at=occurred_at,
        status="complete" if runtime_state is None else runtime_state.status,
        evidence_refs=() if cycle is None else tuple(sorted(protocol.protocol_id for protocol, _ in cycle)),
        rendered_text=rendered_text,
        payload_sha256=hashlib.sha256(rendered_text.encode()).hexdigest(),
    )


def _render_report(
    source_event_id: str,
    cycle: tuple[tuple[StrategyLabProtocol, StrategyLabTraceNode], ...] | None,
    runtime_state: StrategyLabDailyState | None,
) -> str:
    if cycle is None:
        if runtime_state is None:
            raise InvalidStrategyLabDailyReportError("strategy_lab_daily_report_state_missing")
        labs = " ".join(
            f"{lab_id.value}: trace_depth={depth}; outcome=not_evaluated."
            for lab_id, depth in runtime_state.trace_depths
        )
    else:
        labs = " ".join(
            (
                f"{node.body.lab_id.value}: outcome={node.body.result.outcome.value}; "
                f"adaptation={protocol.body.hypothesis.adaptation.value}; feedback={node.body.feedback.value}; "
                f"dataset={protocol.body.dataset_id}; selected_observations={node.body.result.selected_observations}."
            )
            for protocol, node in cycle
        )
    state = _render_runtime_state(cycle, runtime_state)
    text = redact_outbound_text(
        f"Strategy Lab daily summary {source_event_id}. Research-only; profitability claim: false; "
        f"order authority: false. {state} {labs}",
        max_chars=4096,
    ).strip()
    require_safe_outbound_text(text)
    return text


def _render_runtime_state(
    cycle: tuple[tuple[StrategyLabProtocol, StrategyLabTraceNode], ...] | None,
    runtime_state: StrategyLabDailyState | None,
) -> str:
    complete_cycle = "true" if cycle is not None else "false"
    if runtime_state is None:
        return f"complete_cycle={complete_cycle}."
    bundle = "available" if runtime_state.evidence_bundle_available else "missing"
    return (
        f"complete_cycle={complete_cycle}; runtime_status={runtime_state.status}; "
        f"current_cycle={runtime_state.current_cycle}; evidence_bundle={bundle}."
    )


__all__ = (
    "InvalidStrategyLabDailyReportError",
    "StrategyLabDailyState",
    "project_strategy_lab_daily_report",
)
