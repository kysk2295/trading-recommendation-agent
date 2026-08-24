from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from itertools import pairwise
from typing import Final, assert_never

from pydantic import ValidationError

from trading_agent.kr_price_grid import (
    kr_equity_tick_size,
    round_kr_equity_price_down,
    round_kr_equity_price_up,
)
from trading_agent.kr_theme_day_setup_progress import (
    MAX_RECLAIM_BARS,
    RECLAIM_BUFFER,
    SEOUL,
    InvalidKrThemeDaySetupError,
    KrCompletedMinuteBar,
    KrSetupScan,
    KrSetupScanPhase,
    KrThemeDayConditionalSetup,
    KrThemeDaySetupAssessment,
    KrThemeDaySetupInput,
    KrThemeDaySetupPhase,
    scan_kr_theme_day_setup,
)
from trading_agent.kr_theme_day_signal import KrThemeDaySetup
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.signal_contract_models import EvidenceRef, TradeTarget

_SESSION_OPEN: Final = dt.time(9)
_SETUP_VALIDITY: Final = dt.timedelta(seconds=30)
_MAX_EVALUATION_DELAY: Final = dt.timedelta(seconds=30)


def derive_kr_theme_day_setup(source: KrThemeDaySetupInput) -> KrThemeDaySetup | None:
    assessment = assess_kr_theme_day_setup(source)
    match assessment.phase:
        case KrThemeDaySetupPhase.RECLAIM_CONFIRMED:
            return assessment.setup
        case (
            KrThemeDaySetupPhase.NO_IMPULSE
            | KrThemeDaySetupPhase.IMPULSE_ONLY
            | KrThemeDaySetupPhase.PULLBACK_FOUND
            | KrThemeDaySetupPhase.SETUP_EXPIRED
        ):
            return None
        case unreachable:
            assert_never(unreachable)


def assess_kr_theme_day_setup(source: KrThemeDaySetupInput) -> KrThemeDaySetupAssessment:
    request = _validated_input(source)
    _require_point_in_time_lineage(request)
    scan = scan_kr_theme_day_setup(request.bars)
    evidence = _bar_evidence(request)
    match scan.phase:
        case KrSetupScanPhase.NO_IMPULSE:
            return KrThemeDaySetupAssessment(
                phase=KrThemeDaySetupPhase.NO_IMPULSE,
                reason="No completed bar extended one percent above session VWAP.",
                evidence_refs=evidence,
            )
        case KrSetupScanPhase.IMPULSE_ONLY:
            return KrThemeDaySetupAssessment(
                phase=KrThemeDaySetupPhase.IMPULSE_ONLY,
                reason="An impulse exists, but no completed-bar VWAP pullback exists.",
                evidence_refs=evidence,
            )
        case KrSetupScanPhase.PULLBACK_FOUND:
            conditional = _conditional_setup(request, scan, evidence)
            if conditional.valid_until <= request.evaluated_at:
                return KrThemeDaySetupAssessment(
                    phase=KrThemeDaySetupPhase.SETUP_EXPIRED,
                    reason="The completed-bar reclaim window has elapsed.",
                    evidence_refs=evidence,
                )
            return KrThemeDaySetupAssessment(
                phase=KrThemeDaySetupPhase.PULLBACK_FOUND,
                reason="The first completed-bar VWAP pullback is waiting for reclaim confirmation.",
                evidence_refs=evidence,
                conditional=conditional,
            )
        case KrSetupScanPhase.RECLAIM_FOUND:
            pullback, trigger = _scan_bars(request, scan)
            if trigger is None:
                raise InvalidKrThemeDaySetupError
            return KrThemeDaySetupAssessment(
                phase=KrThemeDaySetupPhase.RECLAIM_CONFIRMED,
                reason="The latest completed bar confirmed the reclaim.",
                evidence_refs=evidence,
                setup=_build_setup(request, trigger, pullback),
            )
        case KrSetupScanPhase.SETUP_EXPIRED:
            return KrThemeDaySetupAssessment(
                phase=KrThemeDaySetupPhase.SETUP_EXPIRED,
                reason="The reclaim was invalidated, late, or no longer on the latest completed bar.",
                evidence_refs=evidence,
            )
        case unreachable:
            assert_never(unreachable)


def _validated_input(source: KrThemeDaySetupInput) -> KrThemeDaySetupInput:
    try:
        return KrThemeDaySetupInput.model_validate(source.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDaySetupError from None


def _require_point_in_time_lineage(request: KrThemeDaySetupInput) -> None:
    opportunity = request.opportunity
    bars = request.bars
    first_local = bars[0].start_at.astimezone(SEOUL)
    evidence_ids = tuple(bar.evidence_ref.canonical_id for bar in bars)
    valid = (
        opportunity.strategy_lane == KR_THEME_OPPORTUNITY_LANE
        and opportunity.candidates[0].symbol == bars[0].symbol
        and opportunity.observed_at <= bars[-1].observed_at
        and request.evaluated_at < opportunity.valid_until
        and bars[-1].observed_at <= request.evaluated_at
        and request.evaluated_at - bars[-1].observed_at <= _MAX_EVALUATION_DELAY
        and first_local.time() == _SESSION_OPEN
        and len(evidence_ids) == len(set(evidence_ids))
    )
    contiguous = all(
        current.symbol == bars[0].symbol
        and current.start_at == previous.end_at
        and current.observed_at >= previous.observed_at
        for previous, current in pairwise(bars)
    )
    if not valid or not contiguous:
        raise InvalidKrThemeDaySetupError


def _conditional_setup(
    request: KrThemeDaySetupInput,
    scan: KrSetupScan,
    evidence: tuple[EvidenceRef, ...],
) -> KrThemeDayConditionalSetup:
    pullback, _ = _scan_bars(request, scan)
    threshold = max(pullback.high, scan.current_vwap * (Decimal(1) + RECLAIM_BUFFER))
    trigger = _strict_round_up(threshold)
    stop = round_kr_equity_price_down(pullback.low)
    risk = trigger - stop
    return KrThemeDayConditionalSetup(
        trigger_rule=(
            "A completed bar must close above the current session VWAP reclaim buffer, "
            "trade above the pullback high, close green, and confirm volume."
        ),
        trigger_price=trigger,
        stop_price=stop,
        target_prices=(
            round_kr_equity_price_up(trigger + risk),
            round_kr_equity_price_up(trigger + risk * Decimal(2)),
        ),
        invalidation_rule="Invalidate below the first completed-bar VWAP pullback low or after five reclaim bars.",
        valid_until=min(
            request.opportunity.valid_until,
            pullback.end_at + dt.timedelta(minutes=MAX_RECLAIM_BARS),
        ),
        rationale="A completed-bar VWAP pullback exists, but no current reclaim fill or quote is asserted.",
        evidence_refs=evidence,
    )


def _scan_bars(
    request: KrThemeDaySetupInput,
    scan: KrSetupScan,
) -> tuple[KrCompletedMinuteBar, KrCompletedMinuteBar | None]:
    if scan.pullback_index is None:
        raise InvalidKrThemeDaySetupError
    pullback = request.bars[scan.pullback_index]
    trigger = request.bars[scan.trigger_index] if scan.trigger_index is not None else None
    return pullback, trigger


def _bar_evidence(request: KrThemeDaySetupInput) -> tuple[EvidenceRef, ...]:
    return tuple(sorted((bar.evidence_ref for bar in request.bars), key=lambda item: item.canonical_id))


def _strict_round_up(price: Decimal) -> Decimal:
    rounded = round_kr_equity_price_up(price)
    return rounded if rounded > price else round_kr_equity_price_up(price + kr_equity_tick_size(price))


def _build_setup(
    request: KrThemeDaySetupInput,
    trigger: KrCompletedMinuteBar,
    pullback: KrCompletedMinuteBar,
) -> KrThemeDaySetup:
    risk = trigger.close - pullback.low
    if risk <= 0:
        raise InvalidKrThemeDaySetupError
    evidence = _bar_evidence(request)
    observed_at = trigger.observed_at
    valid_until = min(request.opportunity.valid_until, observed_at + _SETUP_VALIDITY)
    return KrThemeDaySetup(
        setup_id=_setup_id(request, trigger),
        opportunity_id=request.opportunity.opportunity_id,
        producer_strategy_version=request.producer_strategy_version,
        symbol=trigger.symbol,
        observed_at=observed_at,
        valid_until=valid_until,
        stop_price=round_kr_equity_price_down(pullback.low),
        targets=(
            TradeTarget(label="1r", price=round_kr_equity_price_up(trigger.close + risk)),
            TradeTarget(label="2r", price=round_kr_equity_price_up(trigger.close + risk * Decimal(2))),
        ),
        max_slippage_bps=request.max_slippage_bps,
        invalidation_rule="Invalidate below the first completed-bar VWAP pullback low or when a KR market gate blocks.",
        rationale="Fresh rank-one theme leader reclaimed completed-bar session VWAP with volume confirmation.",
        evidence_refs=evidence,
    )


def _setup_id(request: KrThemeDaySetupInput, trigger: KrCompletedMinuteBar) -> str:
    material = "|".join(
        (
            request.opportunity.opportunity_id,
            request.producer_strategy_version,
            trigger.symbol,
            trigger.end_at.isoformat(),
            trigger.evidence_ref.canonical_id,
        )
    )
    return f"kr-theme-vwap-{hashlib.sha256(material.encode()).hexdigest()[:24]}"
