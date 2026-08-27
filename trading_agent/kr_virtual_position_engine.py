from __future__ import annotations

import datetime as dt
from dataclasses import replace
from itertools import pairwise
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.kr_autonomous_trade_models import KrTradeRecommendation
from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar
from trading_agent.kr_virtual_position_models import (
    InvalidKrVirtualPositionError,
    KrVirtualPositionEvent,
    KrVirtualPositionReason,
    KrVirtualPositionState,
    KrVirtualPositionTransition,
    copy_virtual_position_transition,
    partition_virtual_position_bars,
    validate_virtual_position_lineage,
    virtual_position_bars_are_continuous,
    virtual_position_event_id,
    virtual_position_id,
    virtual_position_replay_is_exact,
)

KST: Final = ZoneInfo("Asia/Seoul")
_CLOSE: Final = dt.time(15, 30)


def arm_kr_virtual_position(
    recommendation: KrTradeRecommendation,
    now: dt.datetime,
) -> KrVirtualPositionEvent:
    trusted = KrTradeRecommendation.model_validate(recommendation.model_dump(mode="python"))
    transition = KrVirtualPositionTransition(
        KrVirtualPositionState.ARMED,
        KrVirtualPositionReason.ARMED,
        None,
        None,
        None,
        None,
        None,
        None,
        trusted.evidence_refs,
    )
    return _event(trusted, None, transition, now)


def advance_kr_virtual_position(
    recommendation: KrTradeRecommendation,
    previous: KrVirtualPositionEvent,
    bars: tuple[KrCompletedMinuteBar, ...],
    now: dt.datetime,
) -> tuple[KrVirtualPositionEvent, ...]:
    trusted = KrTradeRecommendation.model_validate(recommendation.model_dump(mode="python"))
    current = KrVirtualPositionEvent.model_validate(previous.model_dump(mode="python"))
    validate_virtual_position_lineage(trusted, current)
    trusted_bars = _completed_bars(trusted, bars, now)
    if current.terminal:
        return ()
    local = trusted.timestamp.astimezone(KST)
    first_start = local.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    replayed, fresh = partition_virtual_position_bars(
        current, tuple(bar for bar in trusted_bars if bar.start_at >= first_start)
    )
    if replayed and not virtual_position_replay_is_exact(current, replayed):
        return (_censored(trusted, current, bars[-1].end_at, KrVirtualPositionReason.DIVERGENT_REPLAY, now),)
    if not fresh:
        if current.state is KrVirtualPositionState.ARMED and now >= trusted.valid_until:
            transition = copy_virtual_position_transition(
                current, KrVirtualPositionState.EXPIRED, KrVirtualPositionReason.ENTRY_EXPIRED
            )
            return (_event(trusted, current, transition, now),)
        return _after_close_or_empty(trusted, current, now)
    if not virtual_position_bars_are_continuous(trusted, current, fresh):
        return (_censored(trusted, current, fresh[-1].end_at, KrVirtualPositionReason.BAR_GAP, now),)
    events: list[KrVirtualPositionEvent] = []
    for bar in fresh:
        transition = _apply_bar(trusted, current, bar)
        current = _event(trusted, current, transition, now)
        events.append(current)
        if current.terminal:
            break
    if not current.terminal:
        events.extend(_after_close_or_empty(trusted, current, now))
    return tuple(events)


def _apply_bar(
    recommendation: KrTradeRecommendation,
    previous: KrVirtualPositionEvent,
    bar: KrCompletedMinuteBar,
) -> KrVirtualPositionTransition:
    evidence = tuple(sorted({*previous.evidence_refs, bar.evidence_ref.canonical_id}))
    if previous.state is KrVirtualPositionState.ARMED:
        if bar.high >= recommendation.entry and bar.low <= recommendation.entry:
            if bar.low <= recommendation.stop:
                return _bar_transition(previous, bar, evidence, KrVirtualPositionState.STOPPED)
            if bar.high >= recommendation.targets[0]:
                return _bar_transition(previous, bar, evidence, KrVirtualPositionState.TARGETED)
            return _bar_transition(previous, bar, evidence, KrVirtualPositionState.ACTIVE)
        if bar.end_at >= recommendation.valid_until:
            return _bar_transition(previous, bar, evidence, KrVirtualPositionState.EXPIRED)
        return _bar_transition(previous, bar, evidence, KrVirtualPositionState.ARMED)
    if bar.low <= recommendation.stop:
        return _bar_transition(previous, bar, evidence, KrVirtualPositionState.STOPPED)
    if bar.high >= recommendation.targets[0]:
        return _bar_transition(previous, bar, evidence, KrVirtualPositionState.TARGETED)
    if bar.end_at.astimezone(KST).time() == _CLOSE:
        return KrVirtualPositionTransition(
            KrVirtualPositionState.EXPIRED,
            KrVirtualPositionReason.SESSION_CLOSE,
            bar.end_at,
            bar.end_at,
            previous.fill_price,
            previous.fill_time,
            bar.close,
            bar.end_at,
            evidence,
        )
    return _bar_transition(previous, bar, evidence, KrVirtualPositionState.ACTIVE)


def _bar_transition(
    previous: KrVirtualPositionEvent,
    bar: KrCompletedMinuteBar,
    evidence: tuple[str, ...],
    state: KrVirtualPositionState,
) -> KrVirtualPositionTransition:
    fill_price = (
        previous.entry
        if previous.fill_price is None
        and state
        in {
            KrVirtualPositionState.ACTIVE,
            KrVirtualPositionState.STOPPED,
            KrVirtualPositionState.TARGETED,
        }
        else previous.fill_price
    )
    fill_time = bar.end_at if previous.fill_time is None and fill_price is not None else previous.fill_time
    reason = {
        KrVirtualPositionState.ARMED: KrVirtualPositionReason.ARMED,
        KrVirtualPositionState.ACTIVE: KrVirtualPositionReason.ENTRY,
        KrVirtualPositionState.STOPPED: KrVirtualPositionReason.STOP_FIRST,
        KrVirtualPositionState.TARGETED: KrVirtualPositionReason.TARGET,
        KrVirtualPositionState.EXPIRED: KrVirtualPositionReason.ENTRY_EXPIRED,
    }[state]
    exit_price = (
        previous.stop
        if state is KrVirtualPositionState.STOPPED
        else (previous.targets[0] if state is KrVirtualPositionState.TARGETED else None)
    )
    return KrVirtualPositionTransition(
        state,
        reason,
        bar.end_at,
        bar.end_at,
        fill_price,
        fill_time,
        exit_price,
        bar.end_at if exit_price is not None else None,
        evidence,
    )


def _event(
    recommendation: KrTradeRecommendation,
    previous: KrVirtualPositionEvent | None,
    transition: KrVirtualPositionTransition,
    now: dt.datetime,
) -> KrVirtualPositionEvent:
    draft = KrVirtualPositionEvent.model_construct(
        event_id="",
        position_id=virtual_position_id(recommendation),
        recommendation_id=recommendation.event_id,
        task_id=recommendation.task_id,
        symbol=recommendation.symbol,
        theme=recommendation.theme,
        sequence=1 if previous is None else previous.sequence + 1,
        previous_event_id=None if previous is None else previous.event_id,
        state=transition.state,
        reason=transition.reason,
        attempted_completed_bar_cursor=transition.attempted,
        accepted_completed_bar_cursor=transition.accepted,
        entry=recommendation.entry,
        stop=recommendation.stop,
        targets=recommendation.targets,
        quantity=recommendation.quantity,
        fill_price=transition.fill_price,
        fill_time=transition.fill_time,
        exit_price=transition.exit_price,
        exit_time=transition.exit_time,
        occurred_at=now,
        evidence_refs=transition.evidence_refs,
    )
    return KrVirtualPositionEvent.model_validate(
        draft.model_copy(update={"event_id": virtual_position_event_id(draft)}).model_dump(mode="python")
    )


def _completed_bars(
    recommendation: KrTradeRecommendation,
    bars: tuple[KrCompletedMinuteBar, ...],
    now: dt.datetime,
) -> tuple[KrCompletedMinuteBar, ...]:
    try:
        trusted = tuple(KrCompletedMinuteBar.model_validate(bar.model_dump(mode="python")) for bar in bars)
    except (ValidationError, ValueError):
        raise InvalidKrVirtualPositionError from None
    session_date = recommendation.timestamp.astimezone(KST).date()
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or now < recommendation.timestamp
        or any(
            bar.end_at > now or bar.observed_at > now or bar.end_at.astimezone(KST).date() != session_date
            for bar in trusted
        )
        or any(left.end_at > right.start_at for left, right in pairwise(trusted))
    ):
        raise InvalidKrVirtualPositionError
    return trusted


def _after_close_or_empty(
    recommendation: KrTradeRecommendation,
    previous: KrVirtualPositionEvent,
    now: dt.datetime,
) -> tuple[KrVirtualPositionEvent, ...]:
    local_date = recommendation.timestamp.astimezone(KST).date()
    close = dt.datetime.combine(local_date, _CLOSE, tzinfo=KST)
    if previous.state is not KrVirtualPositionState.ACTIVE or now < close:
        return ()
    return (_censored(recommendation, previous, close, KrVirtualPositionReason.BAR_GAP, now),)


def _censored(
    recommendation: KrTradeRecommendation,
    previous: KrVirtualPositionEvent,
    attempted: dt.datetime,
    reason: KrVirtualPositionReason,
    now: dt.datetime,
) -> KrVirtualPositionEvent:
    transition = copy_virtual_position_transition(previous, KrVirtualPositionState.CENSORED, reason)
    transition = replace(transition, attempted=attempted)
    return _event(recommendation, previous, transition, now)
