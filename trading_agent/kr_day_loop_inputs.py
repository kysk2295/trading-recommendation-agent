from __future__ import annotations

import hashlib
from typing import Self, override

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from trading_agent.day_agent_challenger_publisher import DayAgentFutureShadowSession
from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.generated_strategy_runtime import GeneratedStrategyRuntimeIdentity
from trading_agent.models import BarInput
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import ProposedHypothesis

_BARS = TypeAdapter(tuple[BarInput, ...])


class InvalidKrDayLoopInputError(ValueError):
    @override
    def __str__(self) -> str:
        return "invalid KR loop authority"


class KrDayLoopResearchBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market_id: MarketId
    instrument_symbol: str = Field(pattern=r"^[0-9]{6}$")
    hypothesis_id: str = Field(pattern=r"^KR-[A-Za-z0-9_.:-]+$")
    experiment_scope_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_source_keys: tuple[str, ...] = Field(min_length=1)
    generated_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replay_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class KrDayLoopInputBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    binding: KrDayLoopResearchBinding
    runtime: GeneratedStrategyRuntimeIdentity
    proposal_template: ProposedHypothesis
    replay_bars: tuple[BarInput, ...] = Field(min_length=1)
    future_sessions: tuple[DayAgentFutureShadowSession, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_kr_authority(self) -> Self:
        registration = self.proposal_template.card.hypothesis
        source_keys = tuple(
            sorted(str(research_source_key(item)) for item in self.proposal_template.cited_sources)
        )
        future_dates = tuple(item.session_date for item in self.future_sessions)
        if (
            self.binding.market_id is not MarketId.KR_EQUITIES
            or registration.hypothesis_id != self.binding.hypothesis_id
            or registration.experiment_scope_key != self.binding.experiment_scope_key
            or self.proposal_template.card.research_source_keys != self.binding.research_source_keys
            or source_keys != self.binding.research_source_keys
            or any(not item.source_id.startswith("kr-") for item in self.proposal_template.cited_sources)
            or hashlib.sha256(self.proposal_template.strategy_draft.source_code.encode()).hexdigest()
            != self.binding.generated_source_sha256
            or hashlib.sha256(_BARS.dump_json(self.replay_bars)).hexdigest()
            != self.binding.replay_sha256
            or any(item.symbol != self.binding.instrument_symbol for item in self.replay_bars)
            or future_dates != tuple(sorted(set(future_dates)))
            or any(
                not item.calendar_snapshot_id.startswith("calendar://official/XKRX/")
                for item in self.future_sessions
            )
        ):
            raise InvalidKrDayLoopInputError
        return self


def build_kr_day_loop_research_binding(
    proposal: ProposedHypothesis,
    replay_bars: tuple[BarInput, ...],
    instrument_symbol: str,
) -> KrDayLoopResearchBinding:
    registration = proposal.card.hypothesis
    return KrDayLoopResearchBinding(
        market_id=MarketId.KR_EQUITIES,
        instrument_symbol=instrument_symbol,
        hypothesis_id=registration.hypothesis_id,
        experiment_scope_key=registration.experiment_scope_key,
        research_source_keys=proposal.card.research_source_keys,
        generated_source_sha256=hashlib.sha256(proposal.strategy_draft.source_code.encode()).hexdigest(),
        replay_sha256=hashlib.sha256(_BARS.dump_json(replay_bars)).hexdigest(),
    )


__all__ = (
    "InvalidKrDayLoopInputError",
    "KrDayLoopInputBundle",
    "KrDayLoopResearchBinding",
    "build_kr_day_loop_research_binding",
)
