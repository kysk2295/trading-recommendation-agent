from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Final, assert_never
from zoneinfo import ZoneInfo

from trading_agent.kis_kr_market_models import (
    KisKrMarketEvidenceError,
    KisKrMarketReceiptKind,
    KisKrMinuteProjectionInput,
)
from trading_agent.kis_kr_market_projection import project_kis_kr_recent_completed_minutes
from trading_agent.kis_kr_market_receipt_store import (
    InvalidKisKrMarketReceiptStoreError,
    KisKrMarketReceiptStore,
)
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths
from trading_agent.kr_autonomous_outcome_classification import outcome_market_state, outcome_session_phase
from trading_agent.kr_autonomous_outcome_models import (
    InvalidKrAutonomousOutcomeError,
    KrAutonomousOutcomeMemory,
    KrOutcomeExecutionState,
    KrOutcomeHorizon,
    KrOutcomeHorizonObservation,
    KrOutcomePriceLevels,
    kr_autonomous_outcome_id,
)
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousNoTrade,
    KrAutonomousRejected,
    KrAutonomousTradeEvent,
    KrTradeRecommendation,
)
from trading_agent.kr_social_signal_models import KrSocialSignal
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar
from trading_agent.kr_virtual_position_models import KrVirtualPositionEvent, KrVirtualPositionState
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore

SEOUL: Final = ZoneInfo("Asia/Seoul")
_HORIZONS: Final = (
    (KrOutcomeHorizon.MINUTES_5, dt.timedelta(minutes=5)),
    (KrOutcomeHorizon.MINUTES_15, dt.timedelta(minutes=15)),
    (KrOutcomeHorizon.MINUTES_30, dt.timedelta(minutes=30)),
)


def build_kr_autonomous_outcome(
    paths: KrAutonomousOperatorPaths,
    event: KrAutonomousTradeEvent,
    now: dt.datetime,
) -> KrAutonomousOutcomeMemory:
    signal = _signal(paths, event)
    position = _position(paths, event)
    horizons = _horizons(event, _bars(paths, event.symbol, now), now)
    refs = tuple(
        sorted(
            {
                event.event_id,
                signal.signal_id,
                *signal.evidence_ids,
                *(item.evidence_ref for item in horizons),
                *(() if position is None else (position.event_id, *position.evidence_refs)),
            }
        )
    )
    observed = max(
        (
            event.timestamp,
            *(item.observed_at for item in horizons),
            *((position.occurred_at,) if position is not None else ()),
        )
    )
    draft = KrAutonomousOutcomeMemory.model_construct(
        outcome_id="",
        task_id=event.task_id,
        trade_event_id=event.event_id,
        position_event_id=None if position is None else position.event_id,
        trade_outcome=event.outcome,
        execution_state=_execution_state(event, position),
        symbol=event.symbol,
        theme=event.theme,
        verification_state=signal.verification_state,
        independent_source_count=signal.independent_source_count,
        independent_source_cluster_ids=signal.independent_source_cluster_ids[:16],
        decision_reason_codes=_reason_codes(event),
        market_evidence_state=outcome_market_state(event),
        session_phase=outcome_session_phase(event.timestamp),
        price_levels=_levels(event),
        horizons=horizons,
        evidence_refs=refs[:32],
        lineage_sha256=hashlib.sha256("|".join(refs).encode()).hexdigest(),
        observed_at=observed,
    )
    return KrAutonomousOutcomeMemory.model_validate(
        draft.model_copy(update={"outcome_id": kr_autonomous_outcome_id(draft)}).model_dump(mode="python")
    )


def outcome_memory_key(outcome: KrAutonomousOutcomeMemory) -> str:
    return f"market.kr.{outcome.symbol}.{outcome.trade_event_id[:24]}"


def _signal(paths: KrAutonomousOperatorPaths, event: KrAutonomousTradeEvent) -> KrSocialSignal:
    matches = tuple(
        signal
        for signal in KrSocialSignalStore(paths.social_signal_database).signals_for_task(event.task_id)
        if signal.symbol == event.symbol and signal.theme == event.theme and signal.normalized_at <= event.timestamp
    )
    if not matches:
        raise InvalidKrAutonomousOutcomeError
    return max(matches, key=lambda item: (item.normalized_at, item.signal_id))


def _position(
    paths: KrAutonomousOperatorPaths,
    event: KrAutonomousTradeEvent,
) -> KrVirtualPositionEvent | None:
    if not isinstance(event, KrTradeRecommendation):
        return None
    matches = tuple(
        item
        for item in KrVirtualPositionStore(paths.position_database).all_events()
        if item.recommendation_id == event.event_id
    )
    return max(matches, key=lambda item: item.sequence, default=None)


def _bars(paths: KrAutonomousOperatorPaths, symbol: str, now: dt.datetime) -> tuple[KrCompletedMinuteBar, ...]:
    try:
        receipts = tuple(
            item
            for item in KisKrMarketReceiptStore(paths.market_receipt_root / f"{symbol}.sqlite3").receipts()
            if item.kind is KisKrMarketReceiptKind.MINUTE_BARS and item.received_at <= now
        )
        if not receipts:
            return ()
        return project_kis_kr_recent_completed_minutes(KisKrMinuteProjectionInput(receipts=receipts, evaluated_at=now))
    except (InvalidKisKrMarketReceiptStoreError, KisKrMarketEvidenceError, ValueError):
        return ()


def _horizons(
    event: KrAutonomousTradeEvent,
    bars: tuple[KrCompletedMinuteBar, ...],
    now: dt.datetime,
) -> tuple[KrOutcomeHorizonObservation, ...]:
    baseline = max((bar for bar in bars if bar.end_at <= event.timestamp), key=lambda item: item.end_at, default=None)
    if baseline is None:
        return ()
    observations = tuple(
        item
        for horizon, delay in _HORIZONS
        if (item := _horizon_observation(horizon, event.timestamp + delay, baseline.close, bars, now)) is not None
    )
    close = _close_observation(event, baseline.close, bars, now)
    return observations if close is None else (*observations, close)


def _horizon_observation(
    horizon: KrOutcomeHorizon,
    target: dt.datetime,
    baseline: Decimal,
    bars: tuple[KrCompletedMinuteBar, ...],
    now: dt.datetime,
) -> KrOutcomeHorizonObservation | None:
    bar = min(
        (item for item in bars if item.end_at >= target and item.observed_at <= now),
        key=lambda item: item.end_at,
        default=None,
    )
    return None if bar is None else _horizon_value(horizon, baseline, bar)


def _close_observation(
    event: KrAutonomousTradeEvent,
    baseline: Decimal,
    bars: tuple[KrCompletedMinuteBar, ...],
    now: dt.datetime,
) -> KrOutcomeHorizonObservation | None:
    local_event = event.timestamp.astimezone(SEOUL)
    local_now = now.astimezone(SEOUL)
    if local_now.date() == local_event.date() and local_now.time() < dt.time(15, 30):
        return None
    eligible = tuple(
        bar
        for bar in bars
        if bar.end_at.astimezone(SEOUL).date() == local_event.date()
        and bar.end_at.astimezone(SEOUL).time() <= dt.time(15, 30)
        and bar.observed_at <= now
    )
    return None if not eligible else _horizon_value(KrOutcomeHorizon.SESSION_CLOSE, baseline, eligible[-1])


def _horizon_value(
    horizon: KrOutcomeHorizon,
    baseline: Decimal,
    bar: KrCompletedMinuteBar,
) -> KrOutcomeHorizonObservation:
    return KrOutcomeHorizonObservation(
        horizon=horizon,
        observed_at=bar.observed_at,
        close=bar.close,
        return_bps=(bar.close / baseline - Decimal(1)) * Decimal(10_000),
        evidence_ref=bar.evidence_ref.canonical_id,
    )


def _levels(event: KrAutonomousTradeEvent) -> KrOutcomePriceLevels | None:
    match event:
        case KrTradeRecommendation():
            return KrOutcomePriceLevels(
                entry=event.entry, stop=event.stop, targets=event.targets, quantity=event.quantity
            )
        case KrAutonomousNoTrade() | KrAutonomousRejected():
            return None
        case unreachable:
            assert_never(unreachable)


def _execution_state(
    event: KrAutonomousTradeEvent,
    position: KrVirtualPositionEvent | None,
) -> KrOutcomeExecutionState:
    match event:
        case KrAutonomousNoTrade():
            return KrOutcomeExecutionState.NO_TRADE
        case KrAutonomousRejected():
            return KrOutcomeExecutionState.REJECTED
        case KrTradeRecommendation():
            if position is None:
                return KrOutcomeExecutionState.RECOMMENDATION_PENDING
            match position.state:
                case KrVirtualPositionState.ARMED:
                    return KrOutcomeExecutionState.VIRTUAL_ARMED
                case KrVirtualPositionState.ACTIVE:
                    return KrOutcomeExecutionState.VIRTUAL_ACTIVE
                case KrVirtualPositionState.STOPPED:
                    return KrOutcomeExecutionState.VIRTUAL_STOPPED
                case KrVirtualPositionState.TARGETED:
                    return KrOutcomeExecutionState.VIRTUAL_TARGETED
                case KrVirtualPositionState.EXPIRED:
                    return KrOutcomeExecutionState.VIRTUAL_EXPIRED
                case KrVirtualPositionState.CENSORED:
                    return KrOutcomeExecutionState.VIRTUAL_CENSORED
                case unreachable:
                    assert_never(unreachable)
        case unreachable:
            assert_never(unreachable)


def _reason_codes(event: KrAutonomousTradeEvent) -> tuple[str, ...]:
    match event:
        case KrTradeRecommendation():
            return tuple(reason.value for reason in event.critic_verdict.reason_codes)
        case KrAutonomousNoTrade():
            return tuple(sorted(reason.value for reason in event.reason_codes))
        case KrAutonomousRejected():
            return tuple(sorted(reason.value for reason in event.reason_codes))
        case unreachable:
            assert_never(unreachable)


__all__ = ("build_kr_autonomous_outcome", "outcome_memory_key")
