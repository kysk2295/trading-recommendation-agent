from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.private_immutable_file import read_private_text
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.strategy_research_types import ResearchAgentId
from trading_agent.us_equity_calendar import NEW_YORK

KST = ZoneInfo("Asia/Seoul")
TARGET_HORIZON = dt.timedelta(minutes=30)
MAXIMUM_SAMPLING_LAG = dt.timedelta(minutes=10)


class StrategyResearchForwardObservationError(ValueError):
    pass


class ForwardResearchObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str = Field(min_length=1)
    market_id: MarketId
    agent_id: Literal[ResearchAgentId.INTRADAY_MOMENTUM] = ResearchAgentId.INTRADAY_MOMENTUM
    source_opportunity_id: str = Field(min_length=1)
    exit_opportunity_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    entered_at: dt.datetime
    target_matured_at: dt.datetime
    observed_at: dt.datetime
    entry_price: Decimal
    exit_price: Decimal
    entry_spread_bps: Decimal
    exit_spread_bps: Decimal
    gross_return: Decimal
    net_return: Decimal
    cluster_key: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    real_market_evidence: Literal[True] = True
    profitability_claim: Literal[False] = False
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        times = (self.entered_at, self.target_matured_at, self.observed_at)
        prices = (self.entry_price, self.exit_price)
        spreads = (self.entry_spread_bps, self.exit_spread_bps)
        if (
            any(value.tzinfo is None or value.utcoffset() is None for value in times)
            or self.target_matured_at - self.entered_at != TARGET_HORIZON
            or not self.target_matured_at <= self.observed_at <= self.target_matured_at + MAXIMUM_SAMPLING_LAG
            or any(not value.is_finite() or value <= 0 for value in prices)
            or any(not value.is_finite() or value < 0 for value in spreads)
            or not self.gross_return.is_finite()
            or not self.net_return.is_finite()
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
        ):
            raise StrategyResearchForwardObservationError("forward_observation_invalid")
        return self


class ForwardResearchObservationJournal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ForwardResearchObservation, ...] = ()


def project_matured_intraday_observations(
    snapshots: tuple[OpportunitySnapshot, ...],
    as_of: dt.datetime,
) -> tuple[ForwardResearchObservation, ...]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise StrategyResearchForwardObservationError("forward_as_of_invalid")
    ordered = tuple(sorted(snapshots, key=lambda item: (item.observed_at, item.opportunity_id)))
    observations: list[ForwardResearchObservation] = []
    for entry in ordered:
        if not _is_momentum(entry):
            continue
        target_matured_at = entry.observed_at + TARGET_HORIZON
        if target_matured_at > as_of:
            continue
        symbol = entry.candidates[0].symbol
        exit_ = next(
            (
                candidate
                for candidate in ordered
                if candidate.strategy_lane.market_id is entry.strategy_lane.market_id
                and candidate.candidates[0].symbol == symbol
                and target_matured_at <= candidate.observed_at <= target_matured_at + MAXIMUM_SAMPLING_LAG
                and _same_session(entry, candidate)
            ),
            None,
        )
        if exit_ is None:
            continue
        observations.append(_observation(entry, exit_, target_matured_at))
    return tuple(observations)


def persist_forward_observations(
    path: Path,
    observations: tuple[ForwardResearchObservation, ...],
) -> int:
    try:
        existing = (
            ForwardResearchObservationJournal()
            if not path.exists()
            else ForwardResearchObservationJournal.model_validate_json(read_private_text(path))
        )
        by_id = {item.observation_id: item for item in existing.items}
        inserted = 0
        for observation in observations:
            previous = by_id.get(observation.observation_id)
            if previous is not None and previous != observation:
                raise StrategyResearchForwardObservationError("forward_observation_conflict")
            if previous is None:
                by_id[observation.observation_id] = observation
                inserted += 1
        journal = ForwardResearchObservationJournal(
            items=tuple(sorted(by_id.values(), key=lambda item: (item.entered_at, item.observation_id)))
        )
        write_private_stable_report(path, journal.model_dump_json() + "\n")
        return inserted
    except (
        OSError,
        StrategyResearchForwardObservationError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise StrategyResearchForwardObservationError("forward_observation_persist_invalid") from None


def load_forward_observations(root: Path) -> tuple[ForwardResearchObservation, ...]:
    try:
        if not root.exists():
            return ()
        journals = tuple(
            session / "strategy-research-forward-observations.json"
            for session in sorted(root.iterdir(), key=lambda item: item.name)[-14:]
            if session.is_dir() and session.name.isdigit() and len(session.name) == 8
        )
        by_id: dict[str, ForwardResearchObservation] = {}
        for path in journals:
            if not path.exists():
                continue
            journal = ForwardResearchObservationJournal.model_validate_json(read_private_text(path))
            for item in journal.items:
                previous = by_id.get(item.observation_id)
                if previous is not None and previous != item:
                    raise StrategyResearchForwardObservationError("forward_observation_conflict")
                by_id[item.observation_id] = item
        return tuple(sorted(by_id.values(), key=lambda item: (item.entered_at, item.observation_id)))
    except (
        OSError,
        StrategyResearchForwardObservationError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise StrategyResearchForwardObservationError("forward_observation_load_invalid") from None


def _observation(
    entry: OpportunitySnapshot,
    exit_: OpportunitySnapshot,
    target_matured_at: dt.datetime,
) -> ForwardResearchObservation:
    entry_price = _feature_decimal(entry, "completed_bar_close")
    exit_price = _feature_decimal(exit_, "completed_bar_close")
    entry_spread = _feature_decimal(entry, "spread_bps")
    exit_spread = _feature_decimal(exit_, "spread_bps")
    gross = exit_price / entry_price - Decimal(1)
    spread_cost = (entry_spread + exit_spread) / Decimal(20_000)
    evidence_refs = tuple(
        sorted(
            {
                *(item.canonical_id for item in entry.evidence_refs),
                *(item.canonical_id for item in exit_.evidence_refs),
            }
        )
    )
    identity = _sha(f"{entry.opportunity_id}:{exit_.opportunity_id}:{target_matured_at.isoformat()}")
    zone = KST if entry.strategy_lane.market_id is MarketId.KR_EQUITIES else NEW_YORK
    return ForwardResearchObservation(
        observation_id=f"forward-intraday-momentum-{identity[:32]}",
        market_id=entry.strategy_lane.market_id,
        source_opportunity_id=entry.opportunity_id,
        exit_opportunity_id=exit_.opportunity_id,
        symbol=entry.candidates[0].symbol,
        entered_at=entry.observed_at,
        target_matured_at=target_matured_at,
        observed_at=exit_.observed_at,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_spread_bps=entry_spread,
        exit_spread_bps=exit_spread,
        gross_return=gross,
        net_return=gross - spread_cost,
        cluster_key=entry.observed_at.astimezone(zone).date().isoformat(),
        evidence_refs=evidence_refs,
    )


def _feature_decimal(snapshot: OpportunitySnapshot, name: str) -> Decimal:
    values = {item.name: item.value for item in snapshot.candidates[0].features}
    try:
        value = Decimal(values[name])
    except (InvalidOperation, KeyError):
        raise StrategyResearchForwardObservationError("forward_feature_missing") from None
    if not value.is_finite():
        raise StrategyResearchForwardObservationError("forward_feature_invalid")
    return value


def _is_momentum(snapshot: OpportunitySnapshot) -> bool:
    return len(snapshot.candidates) == 1 and "momentum" in snapshot.strategy_lane.strategy_id.casefold()


def _same_session(entry: OpportunitySnapshot, exit_: OpportunitySnapshot) -> bool:
    zone = KST if entry.strategy_lane.market_id is MarketId.KR_EQUITIES else NEW_YORK
    return entry.observed_at.astimezone(zone).date() == exit_.observed_at.astimezone(zone).date()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = (
    "ForwardResearchObservation",
    "ForwardResearchObservationJournal",
    "StrategyResearchForwardObservationError",
    "load_forward_observations",
    "persist_forward_observations",
    "project_matured_intraday_observations",
)
