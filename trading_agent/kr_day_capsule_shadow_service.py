from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Final, override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.day_strategy_capsule_models import CapsuleAuthorityCeiling
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowEventPayload,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_intraday_market_gate import KrIntradayGateStatus, assess_kr_shadow_entry
from trading_agent.kr_theme_day_setup import InvalidKrThemeDaySetupError, derive_kr_theme_day_setup
from trading_agent.kr_theme_day_shadow_entry_models import SHADOW_ENTRY_SLIPPAGE_BPS
from trading_agent.kr_theme_day_signal import (
    InvalidKrThemeDaySignalError,
    project_kr_theme_day_shadow_signal,
)

_KST: Final = ZoneInfo("Asia/Seoul")
_ONE_MINUTE: Final = dt.timedelta(minutes=1)
_MAX_BAR_DELAY: Final = dt.timedelta(seconds=30)
_MAX_MARKET_DELAY: Final = dt.timedelta(seconds=5)
_MAX_CAPSULES: Final = 3


class InvalidKrDayCapsuleShadowServiceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day capsule shadow service input is invalid"


@dataclass(frozen=True, slots=True)
class KrDayCapsuleShadowResult:
    created: bool
    event: KrDayCapsuleShadowEvent


@dataclass(frozen=True, slots=True)
class KrDayCapsuleShadowBatchResult:
    results: tuple[KrDayCapsuleShadowResult, ...]


def run_kr_day_capsule_shadow_tick(
    store: KrDayCapsuleShadowStore,
    evaluations: tuple[KrDayCapsuleEvaluation, ...],
) -> KrDayCapsuleShadowBatchResult:
    if not evaluations or len(evaluations) > _MAX_CAPSULES:
        raise InvalidKrDayCapsuleShadowServiceError
    try:
        checked = tuple(
            KrDayCapsuleEvaluation.model_validate(item.model_dump(mode="python")) for item in evaluations
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayCapsuleShadowServiceError from None
    ordered = tuple(sorted(checked, key=lambda item: item.capsule_id))
    if len({item.capsule_id for item in ordered}) != len(ordered):
        raise InvalidKrDayCapsuleShadowServiceError
    anchor = ordered[0]
    shared = (anchor.session_date, anchor.calendar_snapshot_id, anchor.completed_bar_cursor)
    results = tuple(
        _process_one(store, item, shared)
        for item in ordered
    )
    return KrDayCapsuleShadowBatchResult(results)


def _process_one(
    store: KrDayCapsuleShadowStore,
    evaluation: KrDayCapsuleEvaluation,
    shared: tuple[dt.date, str, dt.datetime],
) -> KrDayCapsuleShadowResult:
    replay = store.event_for_evaluation(evaluation.evaluation_id)
    if replay is not None:
        return KrDayCapsuleShadowResult(False, replay)
    previous = store.latest(evaluation.capsule_id, evaluation.session_date.isoformat())
    if previous is not None and previous.terminal:
        return KrDayCapsuleShadowResult(False, previous)
    identity = (evaluation.session_date, evaluation.calendar_snapshot_id, evaluation.completed_bar_cursor)
    if identity != shared:
        return _append(
            store,
            _event(evaluation, previous, KrDayCapsuleShadowStatus.BLOCKED, KrDayCapsuleShadowReason.DIVERGENT_BATCH),
        )
    try:
        _require_evaluation(evaluation)
    except InvalidKrDayCapsuleShadowServiceError:
        return _append(
            store,
            _event(evaluation, previous, KrDayCapsuleShadowStatus.BLOCKED, KrDayCapsuleShadowReason.INVALID_EVALUATION),
        )
    accepted = None if previous is None else previous.accepted_bar_cursor
    if accepted is not None and evaluation.completed_bar_cursor != accepted + _ONE_MINUTE:
        return _append(
            store,
            _event(evaluation, previous, KrDayCapsuleShadowStatus.CENSORED, KrDayCapsuleShadowReason.BAR_GAP),
        )
    if previous is not None and previous.status is KrDayCapsuleShadowStatus.ACTIVE:
        return _append(store, _active_event(evaluation, previous))
    try:
        setup = derive_kr_theme_day_setup(evaluation.setup_input)
        if setup is None:
            projected = _event(
                evaluation,
                previous,
                KrDayCapsuleShadowStatus.REGISTERED,
                KrDayCapsuleShadowReason.NO_SIGNAL,
                accepted_cursor=evaluation.completed_bar_cursor,
            )
        else:
            decision = project_kr_theme_day_shadow_signal(
                evaluation.setup_input.opportunity,
                evaluation.market,
                setup,
                evaluated_at=evaluation.evaluated_at,
            )
            if decision.signal is None:
                projected = _event(
                    evaluation,
                    previous,
                    KrDayCapsuleShadowStatus.BLOCKED,
                    KrDayCapsuleShadowReason.SIGNAL_BLOCKED,
                )
            else:
                signal = decision.signal
                fill = signal.entry_price * (
                    Decimal(1) + SHADOW_ENTRY_SLIPPAGE_BPS / Decimal(10_000)
                )
                projected = _event(
                    evaluation,
                    previous,
                    KrDayCapsuleShadowStatus.ACTIVE,
                    KrDayCapsuleShadowReason.ENTRY,
                    accepted_cursor=evaluation.completed_bar_cursor,
                    signal_id=signal.signal_id,
                    entry_price=fill,
                    stop_price=signal.stop_price,
                    target_prices=tuple(target.price for target in signal.targets),
                )
    except (InvalidKrThemeDaySetupError, InvalidKrThemeDaySignalError):
        projected = _event(
            evaluation,
            previous,
            KrDayCapsuleShadowStatus.FAILED,
            KrDayCapsuleShadowReason.INVALID_EVALUATION,
        )
    return _append(store, projected)


def _active_event(
    evaluation: KrDayCapsuleEvaluation,
    previous: KrDayCapsuleShadowEvent,
) -> KrDayCapsuleShadowEvent:
    bar = evaluation.setup_input.bars[-1]
    if previous.stop_price is None or not previous.target_prices:
        raise InvalidKrDayCapsuleShadowServiceError
    if bar.low <= previous.stop_price:
        status = KrDayCapsuleShadowStatus.STOPPED
        reason = KrDayCapsuleShadowReason.STOP_FIRST
    elif bar.high >= previous.target_prices[0]:
        status = KrDayCapsuleShadowStatus.TARGETED
        reason = KrDayCapsuleShadowReason.TARGET
    else:
        status = KrDayCapsuleShadowStatus.ACTIVE
        reason = KrDayCapsuleShadowReason.ACTIVE
    return _event(
        evaluation,
        previous,
        status,
        reason,
        accepted_cursor=evaluation.completed_bar_cursor,
        signal_id=previous.signal_id,
        entry_price=previous.entry_price,
        stop_price=previous.stop_price,
        target_prices=previous.target_prices,
    )


def _require_evaluation(evaluation: KrDayCapsuleEvaluation) -> None:
    bars = evaluation.setup_input.bars
    latest = bars[-1]
    gate = assess_kr_shadow_entry(evaluation.market, evaluation.evaluated_at)
    if (
        evaluation.authority_ceiling is not CapsuleAuthorityCeiling.RESEARCH_ONLY
        or evaluation.trading_authority is not False
        or evaluation.session_date != evaluation.evaluated_at.astimezone(_KST).date()
        or evaluation.symbol != latest.symbol
        or evaluation.symbol != evaluation.market.symbol
        or evaluation.completed_bar_cursor != latest.end_at
        or latest.end_at > evaluation.evaluated_at
        or latest.observed_at > evaluation.evaluated_at
        or evaluation.evaluated_at - latest.observed_at > _MAX_BAR_DELAY
        or evaluation.market.observed_at > evaluation.evaluated_at
        or evaluation.evaluated_at - evaluation.market.observed_at > _MAX_MARKET_DELAY
        or gate.status is not KrIntradayGateStatus.ELIGIBLE
        or any(current.start_at != previous.end_at for previous, current in pairwise(bars))
    ):
        raise InvalidKrDayCapsuleShadowServiceError


def _event(
    evaluation: KrDayCapsuleEvaluation,
    previous: KrDayCapsuleShadowEvent | None,
    status: KrDayCapsuleShadowStatus,
    reason: KrDayCapsuleShadowReason,
    *,
    accepted_cursor: dt.datetime | None = None,
    signal_id: str | None = None,
    entry_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    target_prices: tuple[Decimal, ...] = (),
) -> KrDayCapsuleShadowEvent:
    latest = evaluation.setup_input.bars[-1]
    payload = KrDayCapsuleShadowEventPayload(
        capsule_id=evaluation.capsule_id,
        evaluation_id=evaluation.evaluation_id,
        session_date=evaluation.session_date,
        calendar_snapshot_id=evaluation.calendar_snapshot_id,
        collection_cycle_id=evaluation.collection_cycle_id,
        symbol=evaluation.symbol,
        attempted_bar_cursor=evaluation.completed_bar_cursor,
        accepted_bar_cursor=accepted_cursor if accepted_cursor is not None else (
            None if previous is None else previous.accepted_bar_cursor
        ),
        previous_event_id=None if previous is None else previous.event_id,
        status=status,
        reason=reason,
        signal_id=signal_id,
        entry_price=entry_price,
        stop_price=stop_price,
        target_prices=target_prices,
        occurred_at=evaluation.evaluated_at,
        evaluation_payload_sha256=hashlib.sha256(
            canonical_experiment_ledger_json(evaluation).encode()
        ).hexdigest(),
        bar_payload_sha256=hashlib.sha256(
            canonical_experiment_ledger_json(latest).encode()
        ).hexdigest(),
    )
    return KrDayCapsuleShadowEvent.model_validate(
        payload.model_dump(mode="python")
        | {"event_id": KrDayCapsuleShadowEvent.canonical_id_for(payload)}
    )


def _append(
    store: KrDayCapsuleShadowStore,
    event: KrDayCapsuleShadowEvent,
) -> KrDayCapsuleShadowResult:
    return KrDayCapsuleShadowResult(store.append(event), event)


__all__ = (
    "InvalidKrDayCapsuleShadowServiceError",
    "KrDayCapsuleShadowBatchResult",
    "KrDayCapsuleShadowResult",
    "run_kr_day_capsule_shadow_tick",
)
