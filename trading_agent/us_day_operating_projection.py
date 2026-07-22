from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import assert_never

from trading_agent.hermes_delivery_models import (
    HERMES_DELIVERY_CONTRACT_VERSION,
    HermesDeliveryKind,
    hermes_delivery_id,
)
from trading_agent.hermes_delivery_projection import HermesProjectionRecord, project_outcomes
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.us_day_operating_models import ProjectedUsDayEvent, UsDayOperatingRequest, UsDayOperatingStatus


def project_us_day_actionable(request: UsDayOperatingRequest, store: HermesDeliveryStore) -> ProjectedUsDayEvent:
    intent = request.order_admission.candidate_intent
    source_id = _source_id("actionable", request, ())
    record = HermesProjectionRecord(
        source_event_id=source_id,
        root_source_event_id=None,
        kind=HermesDeliveryKind.ACTIONABLE,
        market_id="us_equities",
        agent_family="day_trading",
        lane_id=request.lane_id.value,
        strategy_version=request.strategy_version,
        instrument_id=intent.symbol,
        occurred_at=request.evaluated_at,
        status="current_quote_validated",
        evidence_refs=(f"intent:{intent.intent_id}",),
        rendered_text=(
            f"day_trading: {intent.symbol}, entry {intent.entry_limit}, stop {intent.stop}, "
            f"targets {intent.target_1r}, {intent.target_2r}."
        ),
        payload_sha256=request.actionable_payload_sha256,
    )
    with store.writer() as writer:
        _ = project_outcomes((record,), writer)
    return ProjectedUsDayEvent(
        record.source_event_id,
        hermes_delivery_id(record.source_event_id, HERMES_DELIVERY_CONTRACT_VERSION),
    )


def project_us_day_terminal(
    request: UsDayOperatingRequest,
    store: HermesDeliveryStore,
    *,
    status: UsDayOperatingStatus,
    reasons: tuple[str, ...],
    root_source_event_id: str | None,
    occurred_at: dt.datetime,
) -> ProjectedUsDayEvent:
    match status:
        case UsDayOperatingStatus.COMPLETED:
            kind = HermesDeliveryKind.EXIT
        case UsDayOperatingStatus.BLOCKED | UsDayOperatingStatus.INCIDENT:
            kind = HermesDeliveryKind.INCIDENT
        case unreachable:
            assert_never(unreachable)
    intent = request.order_admission.candidate_intent
    source_id = _source_id("terminal", request, reasons)
    material = json.dumps(
        (request.session_id, request.strategy_version, intent.intent_id, status.value, reasons),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    record = HermesProjectionRecord(
        source_event_id=source_id,
        root_source_event_id=root_source_event_id,
        kind=kind,
        market_id="us_equities",
        agent_family="day_trading",
        lane_id=request.lane_id.value,
        strategy_version=request.strategy_version,
        instrument_id=intent.symbol,
        occurred_at=occurred_at,
        status=status.value,
        evidence_refs=(f"intent:{intent.intent_id}",),
        rendered_text=f"day_trading operating result: {intent.symbol} {status.value}.",
        payload_sha256=hashlib.sha256(material.encode()).hexdigest(),
    )
    with store.writer() as writer:
        _ = project_outcomes((record,), writer)
    return ProjectedUsDayEvent(
        record.source_event_id,
        hermes_delivery_id(record.source_event_id, HERMES_DELIVERY_CONTRACT_VERSION),
    )


def _source_id(prefix: str, request: UsDayOperatingRequest, reasons: tuple[str, ...]) -> str:
    intent = request.order_admission.candidate_intent
    material = json.dumps(
        (request.session_id, request.strategy_version, intent.intent_id, prefix, reasons),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return f"us-day-{prefix}-{hashlib.sha256(material.encode()).hexdigest()}"
