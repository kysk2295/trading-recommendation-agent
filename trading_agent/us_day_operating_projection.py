from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import assert_never

from trading_agent.hermes_delivery_errors import HermesDeliveryConflictError
from trading_agent.hermes_delivery_models import (
    HERMES_DELIVERY_CONTRACT_VERSION,
    HermesDeliveryKind,
    hermes_delivery_id,
)
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    delivery_event_from_projection_record,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.us_day_no_setup_source import OrbSessionRecommendation
from trading_agent.us_day_operating_models import ProjectedUsDayEvent, UsDayOperatingRequest, UsDayOperatingStatus


@dataclass(frozen=True, slots=True)
class UsDayMissingTerminalProjection:
    session_id: str
    strategy_version: str
    recommendations: tuple[OrbSessionRecommendation, ...]
    occurred_at: dt.datetime


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


def project_us_day_no_recommendation(
    session_id: str,
    strategy_version: str,
    store: HermesDeliveryStore,
    occurred_at: dt.datetime,
) -> ProjectedUsDayEvent:
    material = json.dumps(
        (session_id, strategy_version, "censored_no_setup"),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    record = HermesProjectionRecord(
        source_event_id=f"us-day-no-recommendation-{digest}",
        root_source_event_id=None,
        kind=HermesDeliveryKind.NO_RECOMMENDATION,
        market_id="us_equities",
        agent_family="day_trading",
        lane_id="intraday_momentum",
        strategy_version=strategy_version,
        instrument_id=None,
        occurred_at=occurred_at,
        status="censored_no_setup",
        evidence_refs=(),
        rendered_text="day_trading operating result: no eligible setup.",
        payload_sha256=digest,
    )
    _project_replayable_outcome(record, store)
    return ProjectedUsDayEvent(
        record.source_event_id,
        hermes_delivery_id(record.source_event_id, HERMES_DELIVERY_CONTRACT_VERSION),
    )


def _project_replayable_outcome(record: HermesProjectionRecord, store: HermesDeliveryStore) -> None:
    try:
        with store.writer() as writer:
            _ = project_outcomes((record,), writer)
    except HermesDeliveryConflictError:
        expected = delivery_event_from_projection_record(record)
        existing = next((item for item in store.events() if item.delivery_id == expected.delivery_id), None)
        if existing is None or existing.model_copy(update={"occurred_at": expected.occurred_at}) != expected:
            raise


def project_us_day_missing_terminal(
    source: UsDayMissingTerminalProjection,
    store: HermesDeliveryStore,
) -> ProjectedUsDayEvent:
    material = json.dumps(
        (
            source.session_id,
            source.strategy_version,
            tuple((item.recommendation_id, item.symbol) for item in source.recommendations),
            "natural_setup_without_terminal",
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    instrument_id = source.recommendations[0].symbol if len(source.recommendations) == 1 else None
    record = HermesProjectionRecord(
        source_event_id=f"us-day-missing-terminal-{digest}",
        root_source_event_id=None,
        kind=HermesDeliveryKind.INCIDENT,
        market_id="us_equities",
        agent_family="day_trading",
        lane_id="intraday_momentum",
        strategy_version=source.strategy_version,
        instrument_id=instrument_id,
        occurred_at=source.occurred_at,
        status="blocked",
        evidence_refs=tuple(f"recommendation:{item.recommendation_id}" for item in source.recommendations),
        rendered_text="day_trading operating result: natural setup has no operating terminal.",
        payload_sha256=digest,
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
