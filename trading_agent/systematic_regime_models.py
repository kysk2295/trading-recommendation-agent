from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.signal_contract_models import EvidenceRef, TradeSignalEnvelope


class InvalidSystematicRegimeModelError(ValueError):
    pass


class RegimeLabel(StrEnum):
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    MIXED = "mixed"


class SystematicDecisionKind(StrEnum):
    RECOMMENDATION = "recommendation"
    NO_RECOMMENDATION = "no_recommendation"


class SystematicMarketContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    context_id: str
    observed_at: dt.datetime
    valid_until: dt.datetime
    regime: RegimeLabel
    equity_breadth_count: int
    equity_breadth_total: Literal[3] = 3
    spy_above_200_session_mean: bool
    spy_20_session_momentum_positive: bool
    producer_version: str
    evidence_ref: EvidenceRef

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if (
            not self.context_id.startswith("us-market-context-")
            or not _aware(self.observed_at)
            or not _aware(self.valid_until)
            or self.valid_until <= self.observed_at
            or not 0 <= self.equity_breadth_count <= self.equity_breadth_total
            or not self.producer_version
            or self.evidence_ref.observed_at != self.observed_at
        ):
            raise InvalidSystematicRegimeModelError
        return self


class SystematicReplayObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_session: dt.date
    target_session: dt.date
    regime: RegimeLabel
    candidate_symbols: tuple[str, ...]
    net_return_bps: Decimal | None

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        recommendation = self.regime is not RegimeLabel.MIXED
        if (
            self.target_session <= self.decision_session
            or recommendation != (len(self.candidate_symbols) == 2)
            or recommendation != (self.net_return_bps is not None)
            or self.candidate_symbols != tuple(sorted(set(self.candidate_symbols)))
            or (self.net_return_bps is not None and not self.net_return_bps.is_finite())
        ):
            raise InvalidSystematicRegimeModelError
        return self


class SystematicReplayResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_key: str
    observed_at: dt.datetime
    round_trip_cost_bps: Decimal
    observations: tuple[SystematicReplayObservation, ...]

    @model_validator(mode="after")
    def validate_replay(self) -> Self:
        pairs = tuple((item.decision_session, item.target_session) for item in self.observations)
        if (
            len(self.source_key) != 64
            or not _aware(self.observed_at)
            or self.round_trip_cost_bps != Decimal("40")
            or not self.observations
            or pairs != tuple(sorted(set(pairs)))
        ):
            raise InvalidSystematicRegimeModelError
        return self

    @property
    def replay_id(self) -> str:
        return _content_hash(self)


class SystematicRecommendationCard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    card_id: str
    strategy_version: str
    observed_at: dt.datetime
    target_session: dt.date
    context: SystematicMarketContext
    decision_kind: SystematicDecisionKind
    candidate_symbols: tuple[str, ...]
    signals: tuple[TradeSignalEnvelope, ...]
    replay_id: str
    order_authority: Literal[False] = False
    account_authority: Literal[False] = False
    allocation_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_card(self) -> Self:
        recommendation = self.decision_kind is SystematicDecisionKind.RECOMMENDATION
        if (
            not self.card_id.startswith("us-systematic-regime-")
            or not self.strategy_version
            or not _aware(self.observed_at)
            or self.context.observed_at != self.observed_at
            or recommendation != (self.context.regime is not RegimeLabel.MIXED)
            or recommendation != (len(self.candidate_symbols) == 2)
            or recommendation != (len(self.signals) == 2)
            or tuple(sorted(signal.symbol for signal in self.signals)) != self.candidate_symbols
            or len(self.replay_id) != 64
        ):
            raise InvalidSystematicRegimeModelError
        return self

    @property
    def artifact_sha256(self) -> str:
        return _content_hash(self)


def _content_hash(value: BaseModel) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidSystematicRegimeModelError",
    "RegimeLabel",
    "SystematicDecisionKind",
    "SystematicMarketContext",
    "SystematicRecommendationCard",
    "SystematicReplayObservation",
    "SystematicReplayResult",
)
