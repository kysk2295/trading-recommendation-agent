from __future__ import annotations

import datetime as dt
import hashlib
from enum import StrEnum
from typing import assert_never, override

from trading_agent.hermes_delivery_errors import (
    HermesDeliveryConflictError,
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionResult,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class UsDayReadinessStatus(StrEnum):
    WAITING_REGULAR_SESSION = "waiting_regular_session"
    READY_REGULAR_SESSION = "ready_regular_session"
    MARKET_CLOSED = "market_closed"
    BLOCKED_RUNTIME = "blocked_runtime"
    BLOCKED_EXISTING_EXPOSURE = "blocked_existing_exposure"
    BLOCKED_MARKET_CLOCK = "blocked_market_clock"


class InvalidUsDayReadinessDeliveryError(ValueError):
    @override
    def __str__(self) -> str:
        return "US Day readiness delivery source is invalid"


def project_us_day_readiness(
    readiness: PaperRuntimeReadiness,
    store: HermesDeliveryStore,
) -> HermesProjectionResult:
    try:
        checked_at = readiness.stream_heartbeat.pong_at
        market_at = readiness.market_clock.market_timestamp
        if not _aware(checked_at) or not _aware(market_at):
            raise InvalidUsDayReadinessDeliveryError
        session_date = market_at.astimezone(NEW_YORK).date()
        status = _status(readiness, market_at)
        source_event_id = f"us-day-readiness-v1:{session_date.isoformat()}:{status.value}"
        with store.writer() as writer:
            existing = tuple(event for event in store.events() if event.source_event_id == source_event_id)
            if len(existing) > 1:
                raise InvalidUsDayReadinessDeliveryError
            occurred_at = checked_at if not existing else existing[0].occurred_at
            record = HermesProjectionRecord(
                source_event_id=source_event_id,
                root_source_event_id=None,
                kind=_kind(status),
                market_id="us_equities",
                agent_family="day_trading",
                lane_id="intraday_momentum",
                strategy_version=None,
                instrument_id=None,
                occurred_at=occurred_at,
                status=status.value,
                evidence_refs=(f"alpaca-paper-readiness:{session_date.isoformat()}",),
                rendered_text=_rendered(status),
                payload_sha256=hashlib.sha256(source_event_id.encode()).hexdigest(),
            )
            return project_outcomes((record,), writer)
    except InvalidUsDayReadinessDeliveryError:
        raise
    except (
        HermesDeliveryConflictError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidHermesDeliveryStoreError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise InvalidUsDayReadinessDeliveryError from None


def _status(readiness: PaperRuntimeReadiness, market_at: dt.datetime) -> UsDayReadinessStatus:
    if not readiness.ready:
        return UsDayReadinessStatus.BLOCKED_RUNTIME
    state = readiness.broker_state
    if state.open_orders or state.positions or state.protective_ocos:
        return UsDayReadinessStatus.BLOCKED_EXISTING_EXPOSURE
    bounds = regular_session_bounds(market_at.astimezone(NEW_YORK).date())
    inside_regular = bounds is not None and bounds[0] <= market_at < bounds[1]
    if readiness.market_clock.is_open != inside_regular:
        return UsDayReadinessStatus.BLOCKED_MARKET_CLOCK
    if readiness.market_clock.is_open:
        return UsDayReadinessStatus.READY_REGULAR_SESSION
    if bounds is not None and market_at < bounds[0]:
        return UsDayReadinessStatus.WAITING_REGULAR_SESSION
    return UsDayReadinessStatus.MARKET_CLOSED


def _kind(status: UsDayReadinessStatus) -> HermesDeliveryKind:
    match status:
        case (
            UsDayReadinessStatus.BLOCKED_RUNTIME
            | UsDayReadinessStatus.BLOCKED_EXISTING_EXPOSURE
            | UsDayReadinessStatus.BLOCKED_MARKET_CLOCK
        ):
            return HermesDeliveryKind.INCIDENT
        case (
            UsDayReadinessStatus.WAITING_REGULAR_SESSION
            | UsDayReadinessStatus.READY_REGULAR_SESSION
            | UsDayReadinessStatus.MARKET_CLOSED
        ):
            return HermesDeliveryKind.DAILY_SUMMARY
        case unreachable:
            assert_never(unreachable)


def _rendered(status: UsDayReadinessStatus) -> str:
    match status:
        case UsDayReadinessStatus.WAITING_REGULAR_SESSION:
            return "US Day Paper 준비상태: read-only 대사 완료, 정규장 개장을 기다립니다. 주문 권한은 없습니다."
        case UsDayReadinessStatus.READY_REGULAR_SESSION:
            return "US Day Paper 준비상태: 정규장 read-only 대사 완료. 이 상태는 주문을 승인하지 않습니다."
        case UsDayReadinessStatus.MARKET_CLOSED:
            return "US Day Paper 준비상태: 시장이 닫혀 있어 주문을 차단했습니다."
        case UsDayReadinessStatus.BLOCKED_RUNTIME:
            return "US Day Paper 준비상태: runtime 대사가 불완전해 주문을 차단했습니다."
        case UsDayReadinessStatus.BLOCKED_EXISTING_EXPOSURE:
            return "US Day Paper 준비상태: 기존 주문 또는 포지션이 있어 신규 주문을 차단했습니다."
        case UsDayReadinessStatus.BLOCKED_MARKET_CLOCK:
            return "US Day Paper 준비상태: broker clock과 공식 세션이 일치하지 않아 주문을 차단했습니다."
        case unreachable:
            assert_never(unreachable)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidUsDayReadinessDeliveryError",
    "UsDayReadinessStatus",
    "project_us_day_readiness",
)
