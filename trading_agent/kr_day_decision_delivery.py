from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.hermes_delivery_projection import (
    HermesProjectionResult,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryWriter
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
)
from trading_agent.kr_day_decision_delivery_identity import same_kr_day_thesis
from trading_agent.kr_day_decision_delivery_records import (
    InvalidKrDayDecisionDeliveryError,
    build_kr_day_decision_records,
)
from trading_agent.kr_day_decision_models import KrDayDecisionEvent
from trading_agent.kr_day_delivery_supplements import (
    KrDayDeliveryIncident,
    build_kr_day_supplement_records,
)


@dataclass(frozen=True, slots=True)
class KrDayDecisionDeliveryBatch:
    decision_events: tuple[KrDayDecisionEvent, ...]
    shadow_events: tuple[KrDayCapsuleShadowEvent, ...]
    incidents: tuple[KrDayDeliveryIncident, ...] = ()
    close_reports: tuple[MarketCloseReport, ...] = ()
    challenger_count: Literal[0, 1] = 0


def project_kr_day_decision_delivery(
    batch: KrDayDecisionDeliveryBatch,
    writer: HermesDeliveryWriter,
) -> HermesProjectionResult:
    try:
        decisions = tuple(
            KrDayDecisionEvent.model_validate(event.model_dump(mode="python"))
            for event in batch.decision_events
        )
        shadows = tuple(
            KrDayCapsuleShadowEvent.model_validate(event.model_dump(mode="python"))
            for event in batch.shadow_events
        )
        incidents = tuple(
            KrDayDeliveryIncident.model_validate(item.model_dump(mode="python"))
            for item in batch.incidents
        )
        reports = tuple(
            MarketCloseReport.model_validate(item.model_dump(mode="python"))
            for item in batch.close_reports
        )
        _require_decision_histories(decisions)
        _require_shadow_histories(shadows)
        records = build_kr_day_decision_records(decisions, shadows)
        records += build_kr_day_supplement_records(
            incidents,
            reports,
            challenger_count=batch.challenger_count,
        )
        return project_outcomes(records, writer)
    except InvalidKrDayDecisionDeliveryError:
        raise
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayDecisionDeliveryError from None


def _require_decision_histories(events: tuple[KrDayDecisionEvent, ...]) -> None:
    latest: dict[tuple[str, str, dt.date], KrDayDecisionEvent] = {}
    seen: set[str] = set()
    for event in events:
        key = (event.capsule_id, event.opportunity_id, event.session_date)
        prior = latest.get(key)
        if event.event_id in seen or event.previous_event_id != (None if prior is None else prior.event_id):
            raise InvalidKrDayDecisionDeliveryError
        if prior is not None and not same_kr_day_thesis(prior, event):
            raise InvalidKrDayDecisionDeliveryError
        seen.add(event.event_id)
        latest[key] = event


def _require_shadow_histories(events: tuple[KrDayCapsuleShadowEvent, ...]) -> None:
    latest: dict[tuple[str, dt.date], KrDayCapsuleShadowEvent] = {}
    active_signals: dict[tuple[str, dt.date], str] = {}
    seen: set[str] = set()
    for event in events:
        key = (event.capsule_id, event.session_date)
        prior = latest.get(key)
        if event.event_id in seen or event.previous_event_id != (None if prior is None else prior.event_id):
            raise InvalidKrDayDecisionDeliveryError
        if prior is not None and (
            event.symbol != prior.symbol
            or event.collection_cycle_id != prior.collection_cycle_id
            or event.calendar_snapshot_id != prior.calendar_snapshot_id
        ):
            raise InvalidKrDayDecisionDeliveryError
        active_signal = active_signals.get(key)
        if active_signal is not None and event.signal_id != active_signal:
            raise InvalidKrDayDecisionDeliveryError
        if event.status.value == "active" and event.signal_id is not None:
            active_signals[key] = event.signal_id
        seen.add(event.event_id)
        latest[key] = event


__all__ = (
    "InvalidKrDayDecisionDeliveryError",
    "KrDayDecisionDeliveryBatch",
    "KrDayDeliveryIncident",
    "project_kr_day_decision_delivery",
)
