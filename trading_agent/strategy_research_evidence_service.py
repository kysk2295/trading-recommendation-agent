from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NewType, Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)
from trading_agent.kr_session_runtime_gate import InvalidKrSessionRuntimeError, require_open_kr_runtime_session
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.research_agent_cycle_models import EvidenceId, MarketId, ResearchAgentEvidenceV1
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.strategy_research_models import EvidenceRef
from trading_agent.strategy_research_observation_builders import SourceAuthorityReceipt
from trading_agent.strategy_research_types import (
    EvidenceKind,
    EvidenceUse,
    LiveEligibilityPolicy,
    ResearchAgentId,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

KST = ZoneInfo("Asia/Seoul")

SourceId = NewType("SourceId", str)
EvidenceQuery = Callable[[], tuple[ResearchAgentEvidenceV1, ...]]


class StrategyResearchEvidenceRejected(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class SourceHypothesisRequest:
    opportunity_evidence_id: EvidenceId
    owner_agent_id: ResearchAgentId
    observed_at: dt.datetime
    source_receipts: tuple[SourceAuthorityReceipt, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceBoundCandidate:
    source_id: SourceId
    symbol: str
    opportunity: OpportunitySnapshot
    source_ref: EvidenceRef


@dataclass(frozen=True, slots=True)
class SourceBoundMarketContext:
    snapshot: MarketContextSnapshot
    source_ref: EvidenceRef


class OpportunityEvidenceService(Protocol):
    def candidate(self, evidence_id: EvidenceId, now: dt.datetime) -> SourceBoundCandidate: ...


class MarketContextEvidenceService(Protocol):
    def current(self, market_id: MarketId, now: dt.datetime) -> SourceBoundMarketContext: ...


class MarketSessionGate(Protocol):
    def require_open(self, market_id: MarketId, now: dt.datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class UsOnlyMarketSessionGate:
    def require_open(self, market_id: MarketId, now: dt.datetime) -> None:
        if market_id != "us_equities":
            raise StrategyResearchEvidenceRejected("kr_session_calendar_missing")
        _require_us_session(now)


@dataclass(frozen=True, slots=True)
class KisKrMarketSessionGate:
    kr_calendar_store: Path

    def require_open(self, market_id: MarketId, now: dt.datetime) -> None:
        if market_id == "us_equities":
            _require_us_session(now)
            return
        if market_id != "kr_equities":
            raise StrategyResearchEvidenceRejected("session_market_unsupported")
        try:
            snapshots = KisKrSessionCalendarStore(self.kr_calendar_store).snapshots()
            current_date = now.astimezone(KST).date()
            matches = tuple(
                snapshot
                for snapshot in snapshots
                if snapshot.payload.observed_at <= now
                and any(day.session_date == current_date for day in snapshot.payload.days)
            )
            if not matches:
                raise StrategyResearchEvidenceRejected("kr_session_calendar_missing")
            snapshot = max(matches, key=lambda item: item.payload.observed_at)
            _ = require_open_kr_runtime_session(snapshot, now)
        except StrategyResearchEvidenceRejected:
            raise
        except (InvalidKisKrSessionCalendarStoreError, InvalidKrSessionRuntimeError, OSError, ValueError):
            raise StrategyResearchEvidenceRejected("session_closed") from None


@dataclass(frozen=True, slots=True)
class CycleStoreOpportunityEvidenceService:
    query: EvidenceQuery
    sessions: MarketSessionGate = UsOnlyMarketSessionGate()

    def candidate(self, evidence_id: EvidenceId, now: dt.datetime) -> SourceBoundCandidate:
        matches = tuple(item for item in self.query() if item.evidence_id == evidence_id)
        if len(matches) != 1:
            raise StrategyResearchEvidenceRejected("opportunity_missing")
        evidence = matches[0]
        if evidence.agent_family_id != "opportunity_manager" or evidence.bounded_payload_json is None:
            raise StrategyResearchEvidenceRejected("opportunity_missing")
        try:
            snapshot = OpportunitySnapshot.model_validate(json.loads(evidence.bounded_payload_json))
        except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
            raise StrategyResearchEvidenceRejected("opportunity_invalid") from None
        market_id = snapshot.strategy_lane.market_id.value
        if evidence.market_id != market_id:
            raise StrategyResearchEvidenceRejected("opportunity_market_mismatch")
        self.sessions.require_open(market_id, now)
        zone = _market_zone(market_id)
        current_date = now.astimezone(zone).date()
        if snapshot.observed_at.astimezone(zone).date() != current_date:
            raise StrategyResearchEvidenceRejected("opportunity_not_current")
        if evidence.available_at > now or snapshot.valid_until < now:
            raise StrategyResearchEvidenceRejected("opportunity_stale")
        latest = max(
            (
                item.observed_at
                for item in self.query()
                if item.agent_family_id == "opportunity_manager"
                and item.market_id == evidence.market_id
                and not item.source_key.startswith("opportunity.blocked.")
            ),
            default=None,
        )
        if latest is None or snapshot.observed_at < latest:
            raise StrategyResearchEvidenceRejected("opportunity_not_latest")
        source = snapshot.evidence_refs[0]
        return SourceBoundCandidate(
            SourceId(source.canonical_id),
            snapshot.candidates[0].symbol,
            snapshot,
            _source_ref(evidence, source.canonical_id, source.observed_at),
        )


@dataclass(frozen=True, slots=True)
class CycleStoreMarketContextEvidenceService:
    query: EvidenceQuery
    sessions: MarketSessionGate = UsOnlyMarketSessionGate()

    def current(self, market_id: MarketId, now: dt.datetime) -> SourceBoundMarketContext:
        self.sessions.require_open(market_id, now)
        candidates = tuple(
            item
            for item in self.query()
            if item.agent_family_id == "market_context"
            and item.market_id == market_id
            and item.bounded_payload_json is not None
            and not item.source_key.startswith("market_context.blocked.")
        )
        if not candidates:
            raise StrategyResearchEvidenceRejected("market_context_missing")
        evidence = max(candidates, key=lambda item: item.observed_at)
        try:
            snapshot = MarketContextSnapshot.model_validate(json.loads(evidence.bounded_payload_json or ""))
        except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
            raise StrategyResearchEvidenceRejected("market_context_invalid") from None
        if snapshot.market_id.value != market_id:
            raise StrategyResearchEvidenceRejected("market_context_market_mismatch")
        zone = _market_zone(market_id)
        current_date = now.astimezone(zone).date()
        if snapshot.observed_at.astimezone(zone).date() != current_date:
            raise StrategyResearchEvidenceRejected("market_context_not_current")
        if evidence.available_at > now or snapshot.valid_until < now:
            raise StrategyResearchEvidenceRejected("market_context_stale")
        source = snapshot.coverage[0]
        return SourceBoundMarketContext(
            snapshot,
            _source_ref(evidence, source.source_id, source.observed_at),
        )


def _source_ref(evidence: ResearchAgentEvidenceV1, source_id: str, as_of: dt.datetime) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=str(evidence.evidence_id),
        source_id=source_id,
        source_kind=EvidenceKind.REAL,
        evidence_use=EvidenceUse.RESEARCH,
        live_eligibility_policy=LiveEligibilityPolicy.TASK3_CURRENT_SESSION_GATE_REQUIRED,
        as_of=as_of,
        available_at=evidence.available_at,
        payload_sha256=evidence.payload_sha256,
    )


def _require_us_session(now: dt.datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise StrategyResearchEvidenceRejected("observation_time_invalid")
    bounds = regular_session_bounds(now.astimezone(NEW_YORK).date())
    if bounds is None or not bounds[0] <= now <= bounds[1]:
        raise StrategyResearchEvidenceRejected("session_closed")


def _market_zone(market_id: MarketId) -> ZoneInfo:
    if market_id == "us_equities":
        return NEW_YORK
    if market_id == "kr_equities":
        return KST
    raise StrategyResearchEvidenceRejected("session_market_unsupported")


__all__ = (
    "CycleStoreMarketContextEvidenceService",
    "CycleStoreOpportunityEvidenceService",
    "KisKrMarketSessionGate",
    "MarketContextEvidenceService",
    "MarketSessionGate",
    "OpportunityEvidenceService",
    "SourceBoundCandidate",
    "SourceBoundMarketContext",
    "SourceHypothesisRequest",
    "StrategyResearchEvidenceRejected",
    "UsOnlyMarketSessionGate",
)
