from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.hermes_delivery_reader import HermesDeliveryReader

_INSTRUMENT = re.compile(r"^(?:[A-Z0-9][A-Z0-9./-]{0,19}|[0-9]{6})$")


class HermesQueryAgentFamily(StrEnum):
    OPPORTUNITY_MANAGER = "opportunity_manager"
    MARKET_CONTEXT = "market_context"
    DAY_TRADING = "day_trading"
    SWING_TRADING = "swing_trading"
    SYSTEMATIC_QUANT = "systematic_quant"
    DERIVATIVES_RESEARCH = "derivatives_research"


class InvalidHermesQueryError(ValueError):
    @override
    def __str__(self) -> str:
        return "Hermes agent query is invalid"


class AgentOpinion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    agent_family: HermesQueryAgentFamily
    lane_id: str | None
    strategy_version: str | None
    status: str
    observed_at: dt.datetime
    evidence_refs: tuple[str, ...]
    summary: str

    @model_validator(mode="after")
    def validate_opinion(self) -> Self:
        if (
            not self.status
            or not self.summary
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
        ):
            raise InvalidHermesQueryError
        return self


class HermesAgentQueryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    instrument_id: str
    observed_at: dt.datetime
    opinions: tuple[AgentOpinion, ...]
    blended_verdict: Literal[None] = None


class HermesAgentQueryService:
    __slots__ = ("_max_age", "_reader")

    def __init__(self, reader: HermesDeliveryReader, *, max_age: dt.timedelta = dt.timedelta(days=1)) -> None:
        if max_age <= dt.timedelta(0):
            raise InvalidHermesQueryError
        self._reader = reader
        self._max_age = max_age

    def query(self, instrument_id: str, *, observed_at: dt.datetime) -> HermesAgentQueryResult:
        if (
            _INSTRUMENT.fullmatch(instrument_id) is None
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            raise InvalidHermesQueryError
        events = tuple(
            event
            for event in self._reader.events()
            if event.occurred_at <= observed_at and event.instrument_id in {None, instrument_id}
        )
        opinions = tuple(self._opinion(family, events, observed_at) for family in HermesQueryAgentFamily)
        return HermesAgentQueryResult(instrument_id=instrument_id, observed_at=observed_at, opinions=opinions)

    def _opinion(self, family: HermesQueryAgentFamily, events, observed_at: dt.datetime) -> AgentOpinion:
        matching = tuple(event for event in events if event.agent_family == family.value)
        if not matching:
            return AgentOpinion(
                agent_family=family,
                lane_id=None,
                strategy_version=None,
                status="blocked_missing_evidence",
                observed_at=observed_at,
                evidence_refs=(),
                summary="No point-in-time evidence is available for this agent.",
            )
        latest = max(matching, key=lambda event: (event.occurred_at, event.delivery_id))
        stale = observed_at - latest.occurred_at > self._max_age
        return AgentOpinion(
            agent_family=family,
            lane_id=latest.lane_id,
            strategy_version=latest.strategy_version,
            status="blocked_stale_projection" if stale else latest.status,
            observed_at=latest.occurred_at,
            evidence_refs=latest.evidence_refs,
            summary="Projection is stale and cannot support a current opinion." if stale else latest.rendered_text,
        )
