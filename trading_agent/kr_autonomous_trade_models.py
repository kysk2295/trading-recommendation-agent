from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self, assert_never, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from trading_agent.kr_autonomous_market_models import KrAutonomousMarketCorroboration
from trading_agent.kr_price_grid import is_valid_kr_equity_price
from trading_agent.kr_social_signal_models import KrSocialSignal, KrSocialVerificationState

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
_SHA = r"^[a-f0-9]{64}$"


class KrAutonomousTradeOutcome(StrEnum):
    RECOMMEND = "RECOMMEND"
    NO_TRADE = "NO_TRADE"
    REJECTED = "REJECTED"


class KrAutonomousSetupKind(StrEnum):
    MOMENTUM_RECLAIM = "momentum_reclaim"
    BREAKOUT_CONTINUATION = "breakout_continuation"


class KrAutonomousCriticStatus(StrEnum):
    APPROVED = "APPROVED"
    MORE_RESEARCH = "MORE_RESEARCH"
    REJECTED = "REJECTED"


class KrNoTradeReason(StrEnum):
    DUPLICATE_SYMBOL = "duplicate_symbol"
    DUPLICATE_THEME = "duplicate_theme"
    STALE_MARKET = "stale_market"
    MISSING_SPREAD = "missing_spread"
    INVALID_STOP = "invalid_stop"
    ZERO_QUANTITY = "zero_quantity"


class KrCriticReason(StrEnum):
    APPROVED = "approved"
    TASK_LINEAGE = "task_lineage"
    SOCIAL_LINEAGE = "social_lineage"
    MARKET_LINEAGE = "market_lineage"
    EVIDENCE_LINEAGE = "evidence_lineage"
    NONCAUSAL_PUBLICATION = "noncausal_publication"
    CLUSTER_COUNT = "cluster_count"
    CURRENT_MARKET = "current_market"
    INVALID_LEVELS = "invalid_levels"
    DUPLICATE_EXPOSURE = "duplicate_exposure"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"


class InvalidKrAutonomousTradeError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR autonomous virtual trade artifact is invalid"


class KrAutonomousTradeThesis(BaseModel):
    model_config = _STRICT

    thesis_id: str = Field(pattern=_SHA)
    task_id: str = Field(pattern=_SHA)
    symbol: str
    theme: str = Field(min_length=1, max_length=160)
    hypothesis: str = Field(min_length=8, max_length=1_000)
    counterevidence: tuple[str, ...] = Field(min_length=1, max_length=16)
    setup_kind: KrAutonomousSetupKind
    social_signal_id: str = Field(pattern=_SHA)
    market_corroboration_id: str = Field(pattern=_SHA)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    submitted_at: AwareDatetime

    @model_validator(mode="after")
    def validate_thesis(self) -> Self:
        if (
            not self.theme.strip()
            or not self.hypothesis.strip()
            or any(not value.strip() for value in self.counterevidence)
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
            or self.thesis_id != thesis_id(self)
        ):
            raise PydanticCustomError("kr_trade_thesis", "KR autonomous trade thesis is invalid")
        return self


class KrOpenVirtualExposure(BaseModel):
    model_config = _STRICT

    symbol: str
    theme: str = Field(min_length=1, max_length=160)


class KrAutonomousTradeRequest(BaseModel):
    model_config = _STRICT

    thesis: KrAutonomousTradeThesis
    social_signal: KrSocialSignal
    market: KrAutonomousMarketCorroboration
    evaluated_at: AwareDatetime
    next_wake_at: AwareDatetime
    open_exposures: tuple[KrOpenVirtualExposure, ...]
    previous_event_id: str | None = Field(default=None, pattern=_SHA)

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.next_wake_at <= self.evaluated_at:
            raise PydanticCustomError("kr_trade_request", "KR autonomous trade request is invalid")
        return self


class KrAutonomousTradeProposal(BaseModel):
    model_config = _STRICT

    proposal_id: str = Field(pattern=_SHA)
    timestamp: AwareDatetime
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, Decimal]
    quantity: int = Field(gt=0)
    rationale: str
    counterevidence: tuple[str, ...]
    verification_state: KrSocialVerificationState
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        if (
            not all(is_valid_kr_equity_price(value) for value in (self.entry, self.stop, *self.targets))
            or not self.stop < self.entry < self.targets[0] < self.targets[1]
            or self.proposal_id != _content_id(self, "proposal_id")
        ):
            raise PydanticCustomError("kr_trade_proposal", "KR autonomous trade proposal is invalid")
        return self


class KrAutonomousCriticVerdict(BaseModel):
    model_config = _STRICT

    verdict_id: str = Field(pattern=_SHA)
    proposal_id: str | None = Field(default=None, pattern=_SHA)
    thesis_id: str = Field(pattern=_SHA)
    status: KrAutonomousCriticStatus
    reason_codes: tuple[KrCriticReason, ...]

    @model_validator(mode="after")
    def validate_verdict(self) -> Self:
        match self.status:
            case KrAutonomousCriticStatus.APPROVED:
                reasons_are_valid = self.reason_codes == (KrCriticReason.APPROVED,)
            case KrAutonomousCriticStatus.MORE_RESEARCH | KrAutonomousCriticStatus.REJECTED:
                reasons_are_valid = KrCriticReason.APPROVED not in self.reason_codes
            case unreachable:
                assert_never(unreachable)
        if not self.reason_codes or not reasons_are_valid or self.verdict_id != _content_id(self, "verdict_id"):
            raise PydanticCustomError("kr_trade_verdict", "KR autonomous Critic verdict is invalid")
        return self


class KrTradeRecommendation(BaseModel):
    model_config = _STRICT

    outcome: Literal[KrAutonomousTradeOutcome.RECOMMEND] = KrAutonomousTradeOutcome.RECOMMEND
    event_id: str = Field(pattern=_SHA)
    plan_id: str = Field(pattern=_SHA)
    previous_event_id: str | None = Field(default=None, pattern=_SHA)
    timestamp: AwareDatetime
    task_id: str = Field(pattern=_SHA)
    thesis_id: str = Field(pattern=_SHA)
    proposal_id: str = Field(pattern=_SHA)
    social_signal_id: str = Field(pattern=_SHA)
    market_corroboration_id: str = Field(pattern=_SHA)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    symbol: str
    theme: str
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, Decimal]
    quantity: int = Field(gt=0)
    rationale: str
    counterevidence: tuple[str, ...]
    verification_state: KrSocialVerificationState
    critic_verdict_id: str = Field(pattern=_SHA)
    critic_verdict: KrAutonomousCriticVerdict
    valid_until: AwareDatetime
    virtual_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        proposal = KrAutonomousTradeProposal.model_construct(
            proposal_id=self.proposal_id,
            timestamp=self.timestamp,
            entry=self.entry,
            stop=self.stop,
            targets=self.targets,
            quantity=self.quantity,
            rationale=self.rationale,
            counterevidence=self.counterevidence,
            verification_state=self.verification_state,
            valid_until=self.valid_until,
        )
        if (
            not all(is_valid_kr_equity_price(value) for value in (self.entry, self.stop, *self.targets))
            or not self.stop < self.entry < self.targets[0] < self.targets[1]
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
            or self.critic_verdict_id != self.critic_verdict.verdict_id
            or self.critic_verdict.status is not KrAutonomousCriticStatus.APPROVED
            or self.critic_verdict.thesis_id != self.thesis_id
            or self.critic_verdict.proposal_id != self.proposal_id
            or self.proposal_id != proposal_id(proposal)
            or self.event_id != event_id(self)
        ):
            raise PydanticCustomError("kr_trade_recommendation", "KR recommendation is invalid")
        return self


class KrAutonomousNoTrade(BaseModel):
    model_config = _STRICT

    outcome: Literal[KrAutonomousTradeOutcome.NO_TRADE] = KrAutonomousTradeOutcome.NO_TRADE
    event_id: str = Field(pattern=_SHA)
    plan_id: str = Field(pattern=_SHA)
    previous_event_id: str | None = Field(default=None, pattern=_SHA)
    timestamp: AwareDatetime
    task_id: str = Field(pattern=_SHA)
    thesis_id: str = Field(pattern=_SHA)
    symbol: str
    theme: str
    reason_codes: tuple[KrNoTradeReason, ...] = Field(min_length=1)
    next_wake_at: AwareDatetime
    virtual_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.next_wake_at <= self.timestamp or self.event_id != event_id(self):
            raise PydanticCustomError("kr_no_trade", "KR no-trade event is invalid")
        return self


class KrAutonomousRejected(BaseModel):
    model_config = _STRICT

    outcome: Literal[KrAutonomousTradeOutcome.REJECTED] = KrAutonomousTradeOutcome.REJECTED
    event_id: str = Field(pattern=_SHA)
    plan_id: str = Field(pattern=_SHA)
    previous_event_id: str | None = Field(default=None, pattern=_SHA)
    timestamp: AwareDatetime
    task_id: str = Field(pattern=_SHA)
    thesis_id: str = Field(pattern=_SHA)
    symbol: str
    theme: str
    reason_codes: tuple[KrCriticReason, ...] = Field(min_length=1)
    critic_verdict_id: str = Field(pattern=_SHA)
    next_wake_at: AwareDatetime
    virtual_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.next_wake_at <= self.timestamp or self.event_id != event_id(self):
            raise PydanticCustomError("kr_trade_rejected", "KR rejected event is invalid")
        return self


type KrAutonomousTradeEvent = Annotated[
    KrTradeRecommendation | KrAutonomousNoTrade | KrAutonomousRejected,
    Field(discriminator="outcome"),
]


def thesis_id(thesis: KrAutonomousTradeThesis) -> str:
    return _content_id(thesis, "thesis_id")


def proposal_id(proposal: KrAutonomousTradeProposal) -> str:
    return _content_id(proposal, "proposal_id")


def verdict_id(verdict: KrAutonomousCriticVerdict) -> str:
    return _content_id(verdict, "verdict_id")


def event_id(event: KrAutonomousTradeEvent) -> str:
    return _content_id(event, "event_id")


def canonical_kr_autonomous_trade_event_json(event: KrAutonomousTradeEvent) -> str:
    return json.dumps(event.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _content_id(model: BaseModel, identity_field: str) -> str:
    payload = json.dumps(
        model.model_dump(mode="json", exclude={identity_field}),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()
