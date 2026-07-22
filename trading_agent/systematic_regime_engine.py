from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Final, override

from pydantic import ValidationError

from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
    TradeTarget,
)
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.systematic_regime_models import (
    RegimeLabel,
    SystematicDecisionKind,
    SystematicMarketContext,
    SystematicRecommendationCard,
    SystematicReplayObservation,
    SystematicReplayResult,
)
from trading_agent.us_equity_calendar import regular_session_bounds

SYSTEMATIC_REGIME_UNIVERSE: Final = ("GLD", "IEF", "IWM", "QQQ", "SHY", "SPY")
SYSTEMATIC_REGIME_LANE: Final = StrategyLaneRef(
    market_id=MarketId.US_EQUITIES,
    agent_family=AgentFamily.SYSTEMATIC_QUANT,
    strategy_id="regime_rotation",
)
MARKET_CONTEXT_VERSION: Final = "us-regime-breadth-200d-50d-v1"
_RISK_ON: Final = ("IWM", "QQQ", "SPY")
_RISK_OFF: Final = ("GLD", "IEF", "SHY")
_ROUND_TRIP_COST_BPS: Final = Decimal("40")


class InvalidSystematicRegimeSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime source is invalid"


@dataclass(frozen=True, slots=True)
class _Decision:
    regime: RegimeLabel
    breadth: int
    spy_above_mean: bool
    spy_momentum_positive: bool
    candidate_symbols: tuple[str, ...]


def replay_systematic_regime(source: SwingDailySource) -> SystematicReplayResult:
    checked, sessions, histories = _checked_source(source)
    observations = tuple(
        _replay_observation(sessions, histories, index)
        for index in range(199, len(sessions) - 1)
    )
    return SystematicReplayResult(
        source_key=checked.source_key,
        observed_at=checked.observed_at,
        round_trip_cost_bps=_ROUND_TRIP_COST_BPS,
        observations=observations,
    )


def build_systematic_card(
    source: SwingDailySource,
    replay: SystematicReplayResult,
    strategy_version: str,
) -> SystematicRecommendationCard:
    checked, sessions, histories = _checked_source(source)
    if replay.source_key != checked.source_key or replay.observed_at != checked.observed_at:
        raise InvalidSystematicRegimeSourceError
    decision = _decision(histories, len(sessions) - 1)
    next_session, valid_until = _next_session(checked.session_date)
    context = _context(checked, decision, valid_until)
    signals = tuple(
        _signal(checked, context, strategy_version, symbol, valid_until)
        for symbol in decision.candidate_symbols
    )
    kind = (
        SystematicDecisionKind.RECOMMENDATION
        if signals
        else SystematicDecisionKind.NO_RECOMMENDATION
    )
    digest = hashlib.sha256(
        f"{strategy_version}|{checked.session_date.isoformat()}|{checked.source_key}".encode()
    ).hexdigest()[:16]
    return SystematicRecommendationCard(
        card_id=f"us-systematic-regime-{checked.session_date:%Y%m%d}-{digest}",
        strategy_version=strategy_version,
        observed_at=checked.observed_at,
        target_session=next_session,
        context=context,
        decision_kind=kind,
        candidate_symbols=tuple(sorted(decision.candidate_symbols)),
        signals=tuple(sorted(signals, key=lambda item: item.symbol)),
        replay_id=replay.replay_id,
    )


def _checked_source(
    source: SwingDailySource,
) -> tuple[SwingDailySource, tuple[dt.date, ...], dict[str, tuple[SwingDailyBar, ...]]]:
    try:
        checked = SwingDailySource.model_validate(source.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidSystematicRegimeSourceError from None
    histories = {symbol: checked.bars_for(symbol) for symbol in checked.symbols}
    sessions = tuple(bar.session_date for bar in histories.get("SPY", ()))
    if (
        checked.symbols != SYSTEMATIC_REGIME_UNIVERSE
        or len(sessions) < 201
        or any(tuple(bar.session_date for bar in history) != sessions for history in histories.values())
        or any(_next_session(current)[0] != following for current, following in pairwise(sessions))
        or sessions[-1] != checked.session_date
    ):
        raise InvalidSystematicRegimeSourceError
    return checked, sessions, histories


def _decision(histories: dict[str, tuple[SwingDailyBar, ...]], index: int) -> _Decision:
    spy = histories["SPY"]
    spy_above_mean = spy[index].close > _mean_close(spy[index - 199 : index + 1])
    spy_momentum_positive = spy[index].close > spy[index - 20].close
    breadth = sum(
        histories[symbol][index].close > _mean_close(histories[symbol][index - 49 : index + 1])
        for symbol in _RISK_ON
    )
    if spy_above_mean and spy_momentum_positive and breadth >= 2:
        regime = RegimeLabel.RISK_ON
        sleeve = _RISK_ON
    elif not spy_above_mean and not spy_momentum_positive and breadth <= 1:
        regime = RegimeLabel.RISK_OFF
        sleeve = _RISK_OFF
    else:
        return _Decision(RegimeLabel.MIXED, breadth, spy_above_mean, spy_momentum_positive, ())
    ranked = sorted(
        sleeve,
        key=lambda symbol: (
            histories[symbol][index].close / histories[symbol][index - 60].close,
            symbol,
        ),
        reverse=True,
    )
    return _Decision(regime, breadth, spy_above_mean, spy_momentum_positive, tuple(ranked[:2]))


def _replay_observation(
    sessions: tuple[dt.date, ...],
    histories: dict[str, tuple[SwingDailyBar, ...]],
    index: int,
) -> SystematicReplayObservation:
    decision = _decision(histories, index)
    returns = tuple(
        (histories[symbol][index + 1].close / histories[symbol][index + 1].open - Decimal(1))
        * Decimal(10_000)
        for symbol in decision.candidate_symbols
    )
    net_return = None if not returns else sum(returns, Decimal(0)) / Decimal(len(returns)) - _ROUND_TRIP_COST_BPS
    return SystematicReplayObservation(
        decision_session=sessions[index],
        target_session=sessions[index + 1],
        regime=decision.regime,
        candidate_symbols=tuple(sorted(decision.candidate_symbols)),
        net_return_bps=net_return,
    )


def _context(
    source: SwingDailySource,
    decision: _Decision,
    valid_until: dt.datetime,
) -> SystematicMarketContext:
    digest = hashlib.sha256(
        f"{MARKET_CONTEXT_VERSION}|{source.session_date}|{source.source_key}".encode()
    ).hexdigest()[:16]
    return SystematicMarketContext(
        context_id=f"us-market-context-{source.session_date:%Y%m%d}-{digest}",
        observed_at=source.observed_at,
        valid_until=valid_until,
        regime=decision.regime,
        equity_breadth_count=decision.breadth,
        spy_above_200_session_mean=decision.spy_above_mean,
        spy_20_session_momentum_positive=decision.spy_momentum_positive,
        producer_version=MARKET_CONTEXT_VERSION,
        evidence_ref=EvidenceRef(
            namespace="systematic/daily_source",
            record_id=source.source_key,
            observed_at=source.observed_at,
        ),
    )


def _signal(
    source: SwingDailySource,
    context: SystematicMarketContext,
    strategy_version: str,
    symbol: str,
    valid_until: dt.datetime,
) -> TradeSignalEnvelope:
    entry = source.bars_for(symbol)[-1].close
    evidence = tuple(
        sorted(
            (
                context.evidence_ref,
                EvidenceRef(
                    namespace="systematic/market_context",
                    record_id=context.context_id,
                    observed_at=source.observed_at,
                ),
            ),
            key=lambda item: item.canonical_id,
        )
    )
    digest = hashlib.sha256(f"{strategy_version}|{source.session_date}|{symbol}".encode()).hexdigest()[:16]
    return TradeSignalEnvelope(
        signal_id=f"systematic-regime-{source.session_date:%Y%m%d}-{symbol}-{digest}",
        strategy_lane=SYSTEMATIC_REGIME_LANE,
        producer_strategy_version=strategy_version,
        symbol=symbol,
        observed_at=source.observed_at,
        valid_until=valid_until,
        side=SignalSide.LONG,
        entry_type=SignalEntryType.LIMIT,
        entry_price=entry,
        stop_price=entry * Decimal("0.92"),
        targets=(TradeTarget(label="target_2r", price=entry * Decimal("1.16")),),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule="Invalidate at the target regular-session close or when the regime changes.",
        rationale="Completed daily trend, breadth, and sleeve momentum agree for next-session shadow evaluation.",
        evidence_refs=evidence,
    )


def _mean_close(bars: tuple[SwingDailyBar, ...]) -> Decimal:
    return sum((bar.close for bar in bars), Decimal(0)) / Decimal(len(bars))


def _next_session(session_date: dt.date) -> tuple[dt.date, dt.datetime]:
    current = session_date + dt.timedelta(days=1)
    for _ in range(14):
        bounds = regular_session_bounds(current)
        if bounds is not None:
            return current, bounds[1]
        current += dt.timedelta(days=1)
    raise InvalidSystematicRegimeSourceError


__all__ = (
    "MARKET_CONTEXT_VERSION",
    "SYSTEMATIC_REGIME_LANE",
    "SYSTEMATIC_REGIME_UNIVERSE",
    "InvalidSystematicRegimeSourceError",
    "build_systematic_card",
    "replay_systematic_regime",
)
