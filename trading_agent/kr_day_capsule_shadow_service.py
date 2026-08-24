from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Final, override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.day_strategy_capsule_models import CapsuleAuthorityCeiling
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
    kr_day_capsule_evaluation_lineage_matches,
)
from trading_agent.kr_day_capsule_shadow_projection import project_kr_day_capsule_shadow_event
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_models import KrDayDecisionReasonCode
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_day_shadow_decision_bridge import (
    KrDayShadowAdmission,
    assess_kr_day_shadow_admission,
)
from trading_agent.kr_intraday_market_gate import KrIntradayGateReason
from trading_agent.kr_theme_day_shadow_entry_models import SHADOW_ENTRY_SLIPPAGE_BPS

_KST: Final = ZoneInfo("Asia/Seoul")
_ONE_MINUTE: Final = dt.timedelta(minutes=1)
_MAX_BAR_DELAY: Final = dt.timedelta(seconds=30)
_MAX_CAPSULES: Final = 3


class InvalidKrDayCapsuleShadowServiceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day capsule shadow service input is invalid"


@dataclass(frozen=True, slots=True)
class KrDayCapsuleShadowResult:
    created: bool
    event: KrDayCapsuleShadowEvent
    decision_event_id: str | None = None
    decision_reason_codes: tuple[KrDayDecisionReasonCode, ...] = ()
    market_gate_reasons: tuple[KrIntradayGateReason, ...] = ()


@dataclass(frozen=True, slots=True)
class KrDayCapsuleShadowBatchResult:
    results: tuple[KrDayCapsuleShadowResult, ...]


def run_kr_day_capsule_shadow_tick(
    store: KrDayCapsuleShadowStore,
    evaluations: tuple[KrDayCapsuleEvaluation, ...],
    decision_store: KrDayDecisionStore,
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
    _require_active_lineages(store, ordered)
    anchor = ordered[0]
    shared = (anchor.session_date, anchor.calendar_snapshot_id, anchor.completed_bar_cursor)
    results = tuple(
        _process_one(store, decision_store, item, shared)
        for item in ordered
    )
    return KrDayCapsuleShadowBatchResult(results)


def _require_active_lineages(
    store: KrDayCapsuleShadowStore,
    evaluations: tuple[KrDayCapsuleEvaluation, ...],
) -> None:
    for evaluation in evaluations:
        previous = store.latest(evaluation.capsule_id, evaluation.session_date.isoformat())
        if previous is not None and previous.status is KrDayCapsuleShadowStatus.ACTIVE and (
            previous.capsule_id,
            previous.session_date,
            previous.symbol,
            previous.collection_cycle_id,
            previous.calendar_snapshot_id,
        ) != (
            evaluation.capsule_id,
            evaluation.session_date,
            evaluation.symbol,
            evaluation.collection_cycle_id,
            evaluation.calendar_snapshot_id,
        ):
            raise InvalidKrDayCapsuleShadowServiceError


def _process_one(
    store: KrDayCapsuleShadowStore,
    decision_store: KrDayDecisionStore,
    evaluation: KrDayCapsuleEvaluation,
    shared: tuple[dt.date, str, dt.datetime],
) -> KrDayCapsuleShadowResult:
    replay = store.event_for_evaluation(evaluation.evaluation_id)
    if replay is not None:
        admission = assess_kr_day_shadow_admission(evaluation, decision_store)
        return _result(False, replay, admission)
    previous = store.latest(evaluation.capsule_id, evaluation.session_date.isoformat())
    if previous is not None and previous.terminal:
        return KrDayCapsuleShadowResult(False, previous)
    identity = (evaluation.session_date, evaluation.calendar_snapshot_id, evaluation.completed_bar_cursor)
    if identity != shared and (
        previous is None or previous.status is not KrDayCapsuleShadowStatus.ACTIVE
    ):
        return _append(
            store,
            project_kr_day_capsule_shadow_event(
                evaluation,
                previous,
                KrDayCapsuleShadowStatus.BLOCKED,
                KrDayCapsuleShadowReason.DIVERGENT_BATCH,
            ),
        )
    try:
        _require_evaluation(evaluation)
    except InvalidKrDayCapsuleShadowServiceError:
        return _append(
            store,
            project_kr_day_capsule_shadow_event(
                evaluation,
                previous,
                KrDayCapsuleShadowStatus.BLOCKED,
                KrDayCapsuleShadowReason.INVALID_EVALUATION,
            ),
        )
    accepted = None if previous is None else previous.accepted_bar_cursor
    if accepted is not None and evaluation.completed_bar_cursor != accepted + _ONE_MINUTE:
        return _append(
            store,
            project_kr_day_capsule_shadow_event(
                evaluation,
                previous,
                KrDayCapsuleShadowStatus.CENSORED,
                KrDayCapsuleShadowReason.BAR_GAP,
            ),
        )
    if previous is not None and previous.status is KrDayCapsuleShadowStatus.ACTIVE:
        return _append(store, _active_event(evaluation, previous))
    admission = assess_kr_day_shadow_admission(evaluation, decision_store)
    if admission.ready:
        if admission.trigger_price is None or admission.stop_price is None:
            raise InvalidKrDayCapsuleShadowServiceError
        fill = admission.trigger_price * (
            Decimal(1) + SHADOW_ENTRY_SLIPPAGE_BPS / Decimal(10_000)
        )
        if not admission.stop_price < fill < admission.target_prices[0]:
            projected = project_kr_day_capsule_shadow_event(
                evaluation,
                previous,
                KrDayCapsuleShadowStatus.REGISTERED,
                KrDayCapsuleShadowReason.INVALID_ENTRY_LADDER,
                accepted_cursor=evaluation.completed_bar_cursor,
            )
        else:
            projected = project_kr_day_capsule_shadow_event(
                evaluation,
                previous,
                KrDayCapsuleShadowStatus.ACTIVE,
                KrDayCapsuleShadowReason.ENTRY,
                accepted_cursor=evaluation.completed_bar_cursor,
                signal_id=f"kr-day-decision-{admission.decision_event_id}",
                entry_price=fill,
                stop_price=admission.stop_price,
                target_prices=admission.target_prices,
            )
    else:
        projected = project_kr_day_capsule_shadow_event(
            evaluation,
            previous,
            KrDayCapsuleShadowStatus.REGISTERED,
            admission.reason,
            accepted_cursor=evaluation.completed_bar_cursor,
        )
    return _append(store, projected, admission)


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
    return project_kr_day_capsule_shadow_event(
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
    if (
        evaluation.authority_ceiling is not CapsuleAuthorityCeiling.RESEARCH_ONLY
        or evaluation.trading_authority is not False
        or not kr_day_capsule_evaluation_lineage_matches(evaluation)
        or evaluation.session_date != evaluation.evaluated_at.astimezone(_KST).date()
        or evaluation.symbol != latest.symbol
        or evaluation.symbol != evaluation.market.symbol
        or evaluation.completed_bar_cursor != latest.end_at
        or latest.end_at > evaluation.evaluated_at
        or latest.observed_at > evaluation.evaluated_at
        or evaluation.evaluated_at - latest.observed_at > _MAX_BAR_DELAY
        or any(current.start_at != previous.end_at for previous, current in pairwise(bars))
    ):
        raise InvalidKrDayCapsuleShadowServiceError


def _append(
    store: KrDayCapsuleShadowStore,
    event: KrDayCapsuleShadowEvent,
    admission: KrDayShadowAdmission | None = None,
) -> KrDayCapsuleShadowResult:
    return _result(store.append(event), event, admission)


def _result(
    created: bool,
    event: KrDayCapsuleShadowEvent,
    admission: KrDayShadowAdmission | None,
) -> KrDayCapsuleShadowResult:
    if admission is None:
        return KrDayCapsuleShadowResult(created, event)
    return KrDayCapsuleShadowResult(
        created,
        event,
        admission.decision_event_id,
        admission.decision_reason_codes,
        admission.market_gate_reasons,
    )


__all__ = (
    "InvalidKrDayCapsuleShadowServiceError",
    "KrDayCapsuleShadowBatchResult",
    "KrDayCapsuleShadowResult",
    "run_kr_day_capsule_shadow_tick",
)
