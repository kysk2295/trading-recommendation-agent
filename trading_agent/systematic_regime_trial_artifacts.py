from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal

from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    TrialKind,
)
from trading_agent.multi_market_experiment_keys import (
    multi_market_hypothesis_registration_key,
    multi_market_strategy_version_registration_key,
)
from trading_agent.multi_market_experiment_models import (
    MultiMarketExperimentScope,
    multi_market_experiment_scope_key,
)
from trading_agent.multi_market_lifecycle_models import MultiMarketStrategyLifecycleEvent
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.systematic_regime_models import SystematicRecommendationCard
from trading_agent.systematic_regime_research import SystematicRegimeResearch
from trading_agent.systematic_regime_store import SystematicShadowOutcome
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


def build_systematic_trial_registration(
    card: SystematicRecommendationCard,
    scope: MultiMarketExperimentScope,
) -> MultiMarketExperimentTrialRegistration:
    digest = hashlib.sha256(f"{card.strategy_version}|{card.target_session}".encode()).hexdigest()[:16]
    data_version = hashlib.sha256(
        f"{card.artifact_sha256}|{card.context.evidence_ref.record_id}".encode()
    ).hexdigest()
    return MultiMarketExperimentTrialRegistration(
        trial_id=f"us-systematic-regime-{card.target_session:%Y%m%d}-{digest}",
        strategy_version=card.strategy_version,
        trial_kind=TrialKind.SHADOW_FORWARD,
        experiment_scope=scope,
        experiment_scope_key=multi_market_experiment_scope_key(scope),
        strategy_lane=scope.primary_lane,
        evaluator_version="systematic_next_session_open_close_v1",
        data_version=data_version,
        feed_entitlement="internal_completed_daily_ohlcv",
        planned_start=card.target_session,
        planned_end=card.target_session,
        registered_at=card.observed_at,
        evidence_budget=("card_1", "market_context_1", "source_1", "terminal_1"),
    )


def build_systematic_lifecycle_event(
    card: SystematicRecommendationCard,
    research: SystematicRegimeResearch,
) -> MultiMarketStrategyLifecycleEvent:
    calendar_id = _calendar_id(card.target_session)
    return MultiMarketStrategyLifecycleEvent(
        strategy_version=card.strategy_version,
        strategy_lane=research.version.strategy_lane,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version="systematic_shadow_lifecycle_v1",
        decision_session_date=card.observed_at.astimezone(NEW_YORK).date(),
        effective_session_date=card.target_session,
        decided_at=card.observed_at,
        session_calendar_snapshot_id=calendar_id,
        evidence_keys=tuple(
            sorted(
                (
                    calendar_id,
                    research.hypothesis.experiment_scope_key,
                    str(multi_market_hypothesis_registration_key(research.hypothesis)),
                    str(multi_market_strategy_version_registration_key(research.version)),
                )
            )
        ),
        reason_codes=("multi_market_strategy_registered",),
        previous_event_key=None,
    )


def build_systematic_shadow_outcome(
    card: SystematicRecommendationCard,
    source: SwingDailySource,
) -> SystematicShadowOutcome:
    returns = tuple(
        (source.bars_for(symbol)[-1].close / source.bars_for(symbol)[-1].open - Decimal(1))
        * Decimal(10_000)
        for symbol in card.candidate_symbols
    )
    net = None if not returns else sum(returns, Decimal(0)) / Decimal(len(returns)) - Decimal(40)
    return SystematicShadowOutcome(
        card_id=card.card_id,
        target_session=card.target_session,
        observed_at=source.observed_at,
        candidate_symbols=card.candidate_symbols,
        no_position=not returns,
        net_return_bps=net,
        source_key=source.source_key,
    )


def _calendar_id(session_date: dt.date) -> str:
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        raise ValueError("invalid systematic target session")
    material = f"nyse-calendar-v1|{bounds[0].isoformat()}|{bounds[1].isoformat()}"
    return hashlib.sha256(material.encode()).hexdigest()


__all__ = (
    "build_systematic_lifecycle_event",
    "build_systematic_shadow_outcome",
    "build_systematic_trial_registration",
)
