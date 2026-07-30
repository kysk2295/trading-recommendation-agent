from __future__ import annotations

import datetime as dt
import re
import sqlite3
from enum import StrEnum
from typing import Literal, Protocol, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.dashboard_reviewer_lifecycle import PersistedChampionAuthority
from trading_agent.experiment_ledger_store import (
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.lane_review_store import (
    InvalidLaneReviewSourceError,
    UnsupportedLaneReviewSchemaError,
)

_INSTRUMENT = re.compile(r"^(?:[A-Z0-9][A-Z0-9./-]{0,19}|[0-9]{6})$")


class HermesQueryAgentFamily(StrEnum):
    OPPORTUNITY_MANAGER = "opportunity_manager"
    MARKET_CONTEXT = "market_context"
    DAY_TRADING = "day_trading"
    SWING_TRADING = "swing_trading"
    SYSTEMATIC_QUANT = "systematic_quant"
    DERIVATIVES_RESEARCH = "derivatives_research"


class AllocationManagerState(StrEnum):
    DISABLED = "disabled"
    AVAILABLE = "available"


class AllocationManagerReason(StrEnum):
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    TWO_INDEPENDENT_CHAMPIONS_REQUIRED = "two_independent_champions_required"
    TWO_INDEPENDENT_CHAMPIONS_PRESENT = "two_independent_champions_present"


class AllocationManagerAuthority(Protocol):
    def champions(self) -> tuple[PersistedChampionAuthority, ...]: ...


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


class AllocationManagerStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    state: AllocationManagerState
    reason: AllocationManagerReason
    independent_champion_count: int
    required_independent_champion_count: Literal[2] = 2
    evidence_refs: tuple[str, ...]
    direct_order_authority: Literal[False] = False
    symbol_selection_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if (
            self.independent_champion_count < 0
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
        ):
            raise InvalidHermesQueryError
        match self.reason:
            case AllocationManagerReason.AUTHORITY_UNAVAILABLE:
                valid = (
                    self.state is AllocationManagerState.DISABLED
                    and self.independent_champion_count == 0
                    and not self.evidence_refs
                )
            case AllocationManagerReason.TWO_INDEPENDENT_CHAMPIONS_REQUIRED:
                valid = (
                    self.state is AllocationManagerState.DISABLED
                    and self.independent_champion_count < self.required_independent_champion_count
                )
            case AllocationManagerReason.TWO_INDEPENDENT_CHAMPIONS_PRESENT:
                valid = (
                    self.state is AllocationManagerState.AVAILABLE
                    and self.independent_champion_count >= self.required_independent_champion_count
                )
        if not valid:
            raise InvalidHermesQueryError
        return self


class HermesAgentQueryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    instrument_id: str
    observed_at: dt.datetime
    opinions: tuple[AgentOpinion, ...]
    allocation_manager: AllocationManagerStatus
    blended_verdict: Literal[None] = None


class HermesAgentQueryService:
    __slots__ = ("_allocation_authority", "_max_age", "_reader")

    def __init__(
        self,
        reader: HermesDeliveryReader,
        *,
        allocation_authority: AllocationManagerAuthority | None = None,
        max_age: dt.timedelta = dt.timedelta(days=1),
    ) -> None:
        if max_age <= dt.timedelta(0):
            raise InvalidHermesQueryError
        self._reader = reader
        self._allocation_authority = allocation_authority
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
        return HermesAgentQueryResult(
            instrument_id=instrument_id,
            observed_at=observed_at,
            opinions=opinions,
            allocation_manager=self._allocation_status(),
        )

    def _allocation_status(self) -> AllocationManagerStatus:
        if self._allocation_authority is None:
            return AllocationManagerStatus(
                state=AllocationManagerState.DISABLED,
                reason=AllocationManagerReason.AUTHORITY_UNAVAILABLE,
                independent_champion_count=0,
                evidence_refs=(),
            )
        try:
            champions = self._allocation_authority.champions()
        except (
            InvalidExperimentLedgerSourceError,
            UnsupportedExperimentLedgerSchemaError,
            InvalidLaneReviewSourceError,
            UnsupportedLaneReviewSchemaError,
            sqlite3.DatabaseError,
            ValidationError,
        ):
            return AllocationManagerStatus(
                state=AllocationManagerState.DISABLED,
                reason=AllocationManagerReason.AUTHORITY_UNAVAILABLE,
                independent_champion_count=0,
                evidence_refs=(),
            )
        independent_count = len({(champion.family_id, champion.lane_id) for champion in champions})
        evidence_refs = tuple(
            sorted(
                {
                    evidence_ref
                    for champion in champions
                    for evidence_ref in (champion.lifecycle_ref, champion.reviewer_ref)
                }
            )
        )
        if independent_count >= 2:
            return AllocationManagerStatus(
                state=AllocationManagerState.AVAILABLE,
                reason=AllocationManagerReason.TWO_INDEPENDENT_CHAMPIONS_PRESENT,
                independent_champion_count=independent_count,
                evidence_refs=evidence_refs,
            )
        return AllocationManagerStatus(
            state=AllocationManagerState.DISABLED,
            reason=AllocationManagerReason.TWO_INDEPENDENT_CHAMPIONS_REQUIRED,
            independent_champion_count=independent_count,
            evidence_refs=evidence_refs,
        )

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
