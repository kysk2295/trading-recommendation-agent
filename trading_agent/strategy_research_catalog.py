from __future__ import annotations

from typing import Final

from pydantic import Field

from trading_agent.strategy_research_types import CanonicalModel, ResearchAgentId


class ResearchCadence(CanonicalModel):
    trigger: str = Field(min_length=1)
    delay_minutes: int = Field(ge=0, le=24 * 60)
    maturity_gate: str = Field(min_length=1)
    schedule_scope: str = Field(min_length=1)


class StrategyResearchIdentity(CanonicalModel):
    agent_id: ResearchAgentId
    identity: str = Field(min_length=1)
    methodology: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    cadence: ResearchCadence


STRATEGY_RESEARCH_CATALOG: Final = (
    StrategyResearchIdentity(
        agent_id=ResearchAgentId.INTRADAY_MOMENTUM,
        identity="intraday trend continuation researcher",
        methodology="latest-completed-bar breakout and continuation",
        output_contract="cost-aware same-session intraday protocol",
        cadence=ResearchCadence(
            trigger="completed liquid five-minute bar with fresh spread",
            delay_minutes=5,
            maturity_gate="decision-time momentum is available after bar completion",
            schedule_scope="each eligible New York trading-session bar",
        ),
    ),
    StrategyResearchIdentity(
        agent_id=ResearchAgentId.INTRADAY_MEAN_REVERSION,
        identity="intraday dislocation reversion researcher",
        methodology="completed-bar spread or residual dislocation and reversion",
        output_contract="bounded-horizon protocol with stop-first same-bar collision",
        cadence=ResearchCadence(
            trigger="completed five-minute bar after displacement and coverage gate",
            delay_minutes=5,
            maturity_gate="dislocation and actionable spread coverage are both complete",
            schedule_scope="each eligible New York trading-session displacement",
        ),
    ),
    StrategyResearchIdentity(
        agent_id=ResearchAgentId.CATALYST_EVENT,
        identity="point-in-time catalyst response researcher",
        methodology="verified disclosure or news surprise event window",
        output_contract="censoring-aware post-event protocol",
        cadence=ResearchCadence(
            trigger="new immutable catalyst receipt",
            delay_minutes=15,
            maturity_gate="post-event maturity reached during an eligible session",
            schedule_scope="each novel verified catalyst; wait while session is closed",
        ),
    ),
    StrategyResearchIdentity(
        agent_id=ResearchAgentId.SWING_TREND_REGIME,
        identity="multi-session trend and regime researcher",
        methodology="daily trend conditional on an ex-ante regime",
        output_contract="next-session-forward multi-session protocol",
        cadence=ResearchCadence(
            trigger="completed NYSE session or ex-ante regime change",
            delay_minutes=30,
            maturity_gate="daily bar and regime context are final for the session",
            schedule_scope="once per completed New York trading session",
        ),
    ),
    StrategyResearchIdentity(
        agent_id=ResearchAgentId.CROSS_SECTIONAL_QUANT,
        identity="point-in-time cross-sectional ranking researcher",
        methodology="same-timestamp universe rank spread",
        output_contract="sector and turnover neutral cross-sectional protocol",
        cadence=ResearchCadence(
            trigger="point-in-time universe snapshot maturity",
            delay_minutes=45,
            maturity_gate="membership, ranks, sector, and turnover inputs share one timestamp",
            schedule_scope="once per completed session when a mature snapshot exists",
        ),
    ),
    StrategyResearchIdentity(
        agent_id=ResearchAgentId.DERIVATIVES_VOLATILITY,
        identity="derivatives and volatility surface researcher",
        methodology="implied-realized, skew, and term-structure cross-check",
        output_contract="hedge-convention and maturity-aware net-cost protocol",
        cadence=ResearchCadence(
            trigger="new complete option or futures surface",
            delay_minutes=0,
            maturity_gate="official option close and quote-authority coverage are final",
            schedule_scope="once per completed derivatives session boundary",
        ),
    ),
)


__all__ = (
    "STRATEGY_RESEARCH_CATALOG",
    "ResearchCadence",
    "StrategyResearchIdentity",
)
