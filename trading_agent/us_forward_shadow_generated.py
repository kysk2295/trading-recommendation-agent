from __future__ import annotations

import datetime as dt
from decimal import Decimal

from trading_agent.day_forward_probe_bridge import (
    DayCompletedBarLineage,
    DayTargetProjectionPolicy,
    DayTargetRule,
    DayTradeSignalProjection,
    DayTradeSignalProjectionRequest,
    project_day_trade_signal,
)
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.generated_strategy_artifact import PublishedGeneratedStrategy
from trading_agent.generated_strategy_protocol import BarFrame, CandidateFrame
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.models import BarInput, MomentumCandidate
from trading_agent.research_identity_models import MarketId
from trading_agent.us_forward_shadow_models import UsForwardShadowTick
from trading_agent.us_forward_shadow_services import (
    InvalidUsForwardShadowRuntimeError,
    UsForwardShadowServices,
)
from trading_agent.us_forward_shadow_trial import completed_bar_at


def evaluate_generated_signal(
    tick: UsForwardShadowTick,
    capsule: StrategyCapsule,
    services: UsForwardShadowServices,
    *,
    evaluation_at: dt.datetime,
) -> DayTradeSignalProjection | None:
    observation_at = completed_bar_at(tick)
    generated_id = capsule.generated_artifact_id
    if generated_id is None:
        raise InvalidUsForwardShadowRuntimeError("capsule_artifact_missing")
    artifact = services.generated_artifacts.load(generated_id)
    published = PublishedGeneratedStrategy(
        artifact=artifact,
        source_path=services.generated_artifacts.root / generated_id / "strategy.py",
        manifest_path=services.generated_artifacts.root / generated_id / "manifest.json",
        created=False,
    )
    sandbox = GeneratedStrategySandbox(
        services.generated_artifacts.runtime,
        services.task_root,
        capsule.resource_limits.to_generated_limits(),
    )
    with sandbox.open_session(published) as session:
        for bar in tick.bars[:-1]:
            _ = session.observe(_bar_input(bar), None)
        candidate = None if tick.candidate is None else _candidate_input(tick.candidate)
        signal_candidate = session.observe(_bar_input(tick.bars[-1]), candidate)
    if signal_candidate is None:
        return None
    return project_day_trade_signal(
        DayTradeSignalProjectionRequest(
            capsule=capsule,
            candidate=signal_candidate,
            completed_bar=DayCompletedBarLineage(
                market_id=MarketId.US_EQUITIES,
                bar=tick.bars[-1],
                valid_until=observation_at + dt.timedelta(seconds=30),
                record_id=tick.completed_bar_id,
            ),
            observed_at=observation_at,
            quote_validation=tick.quote,
            target_policy=DayTargetProjectionPolicy(
                rules=(
                    DayTargetRule(label="r1", reward_risk_multiple=Decimal("1")),
                    DayTargetRule(label="r2", reward_risk_multiple=Decimal("2")),
                ),
                valid_for=dt.timedelta(minutes=1),
            ),
            evidence_refs=tick.evidence_refs,
        )
    )


def _bar_input(bar: BarFrame) -> BarInput:
    return BarInput(
        symbol=bar.symbol,
        timestamp=bar.timestamp,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        prior_close=bar.prior_close,
        average_daily_volume=bar.average_daily_volume,
        spread_bps=bar.spread_bps,
        catalyst=bar.catalyst,
    )


def _candidate_input(candidate: CandidateFrame) -> MomentumCandidate:
    return MomentumCandidate(
        symbol=candidate.symbol,
        timestamp=candidate.timestamp,
        price=candidate.price,
        gap_pct=candidate.gap_pct,
        change_pct=candidate.change_pct,
        relative_volume=candidate.relative_volume,
        cumulative_dollar_volume=candidate.cumulative_dollar_volume,
        spread_bps=candidate.spread_bps,
        catalyst=candidate.catalyst,
    )


__all__ = ("evaluate_generated_signal",)
