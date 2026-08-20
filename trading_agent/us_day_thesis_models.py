from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import EvidenceRef, QuoteValidation, TradeTarget
from trading_agent.us_day_situation_models import FlowObservationKind, UsDaySituationMap

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_REASON = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9./-]{0,19}$")


class DayTradeDecision(StrEnum):
    RECOMMEND = "recommend"
    WATCH = "watch"
    NO_TRADE = "no_trade"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ThesisChangeKind(StrEnum):
    HOLD = "hold"
    CANCEL_ENTRY = "cancel_entry"
    INVALIDATE_LOGIC = "invalidate_logic"
    PARTIAL_EXIT = "partial_exit"
    CLOSE = "close"


class UsDayPlaybook(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    playbook_id: str
    title: str = Field(min_length=1, max_length=80)
    entry_type: Literal["stop_trigger", "limit"]

    @model_validator(mode="after")
    def validate_playbook(self) -> Self:
        if _TOKEN.fullmatch(self.playbook_id) is None or self.title != self.title.strip():
            raise ValueError("invalid US day playbook")
        return self


class UsDayChampion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    strategy_lane: StrategyLaneRef
    deployed: Literal[True]
    playbooks: tuple[UsDayPlaybook, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_champion(self) -> Self:
        ids = tuple(item.playbook_id for item in self.playbooks)
        if (
            self.strategy_lane.market_id is not MarketId.US_EQUITIES
            or self.strategy_lane.agent_family is not AgentFamily.DAY_TRADING
            or ids != tuple(sorted(set(ids)))
        ):
            raise ValueError("invalid deployed US day Champion")
        return self


class EvidenceBoundRationale(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=240)
    observation_kind: FlowObservationKind
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_rationale(self) -> Self:
        identities = tuple(item.canonical_id for item in self.evidence_refs)
        if self.text != self.text.strip() or any(character in self.text for character in "\r\n\t"):
            raise ValueError("invalid rationale text")
        if identities != tuple(sorted(set(identities))):
            raise ValueError("rationale evidence is not canonical")
        return self


class UsDayCurrentMarket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str
    quote: QuoteValidation
    quote_ref: EvidenceRef
    current_bar_ref: EvidenceRef
    allowed_prices: tuple[Decimal, ...] = Field(min_length=4, max_length=16)

    @model_validator(mode="after")
    def validate_market(self) -> Self:
        if (
            _SYMBOL.fullmatch(self.symbol) is None
            or self.quote_ref.namespace != "quote/snapshot"
            or self.current_bar_ref.namespace != "research/current_bar"
            or any(not item.is_finite() or item <= 0 for item in self.allowed_prices)
            or self.allowed_prices != tuple(sorted(set(self.allowed_prices)))
            or self.quote.bid not in self.allowed_prices
            or self.quote.ask not in self.allowed_prices
        ):
            raise ValueError("invalid current market evidence")
        return self


class UsDayTradeThesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1] = 1
    thesis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: DayTradeDecision
    situation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    playbook_id: str
    theme_name: str = Field(min_length=1, max_length=80)
    symbol: str | None
    entry_price: Decimal | None
    stop_price: Decimal | None
    targets: tuple[TradeTarget, ...] = Field(max_length=8)
    invalidation_rule: str = Field(min_length=1, max_length=240)
    confidence_bps: int = Field(ge=0, le=10_000)
    evidence_refs: tuple[EvidenceRef, ...]
    observed_at: AwareDatetime
    valid_until: AwareDatetime
    reason_code: str | None
    theme_rationale: EvidenceBoundRationale | None = None
    catalyst_rationale: EvidenceBoundRationale | None = None
    leader_rationale: EvidenceBoundRationale | None = None
    flow_rationale: EvidenceBoundRationale | None = None
    order_authority: Literal[False] = False

    @field_validator("observed_at", "valid_until")
    @classmethod
    def canonical_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_thesis(self, info: ValidationInfo) -> Self:
        recommend = self.decision is DayTradeDecision.RECOMMEND
        rationales = (
            self.theme_rationale,
            self.catalyst_rationale,
            self.leader_rationale,
            self.flow_rationale,
        )
        recommendation_valid = (
            self.symbol is not None
            and _SYMBOL.fullmatch(self.symbol) is not None
            and self.entry_price is not None
            and self.stop_price is not None
            and self.entry_price.is_finite()
            and self.stop_price.is_finite()
            and self.stop_price < self.entry_price
            and len(self.targets) >= 2
            and tuple(item.price for item in self.targets) == tuple(sorted(set(item.price for item in self.targets)))
            and all(item.price > self.entry_price for item in self.targets)
            and self.reason_code is None
            and all(item is not None for item in rationales)
        )
        terminal_valid = (
            self.symbol is None
            and self.entry_price is None
            and self.stop_price is None
            and not self.targets
            and self.reason_code is not None
            and _REASON.fullmatch(self.reason_code) is not None
            and all(item is None for item in rationales)
        )
        evidence_ids = tuple(item.canonical_id for item in self.evidence_refs)
        expected_evidence = _canonical_refs(
            tuple(ref for rationale in rationales if rationale is not None for ref in rationale.evidence_refs)
        )
        if (
            _TOKEN.fullmatch(self.playbook_id) is None
            or _TOKEN.fullmatch(self.theme_name) is None
            or self.invalidation_rule != self.invalidation_rule.strip()
            or any(character in self.invalidation_rule for character in "\r\n\t")
            or self.valid_until <= self.observed_at
            or (recommend and not recommendation_valid)
            or (not recommend and not terminal_valid)
            or evidence_ids != tuple(sorted(set(evidence_ids)))
            or self.evidence_refs != expected_evidence
            or any(ref.observed_at > self.observed_at for ref in self.evidence_refs)
            or (
                not bool(info.context and info.context.get("skip_identity"))
                and self.thesis_id != self.canonical_id_for(self.model_dump(mode="python"))
            )
        ):
            raise ValueError("invalid US day trade thesis")
        return self

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, object]) -> str:
        return _content_id(payload, "thesis_id")

    @classmethod
    def create(cls, **payload: object) -> UsDayTradeThesis:
        candidate = {
            "schema_version": 1,
            "theme_rationale": None,
            "catalyst_rationale": None,
            "leader_rationale": None,
            "flow_rationale": None,
            "order_authority": False,
            **payload,
            "thesis_id": "0" * 64,
        }
        normalized = cls.model_validate(candidate, context={"skip_identity": True})
        candidate = normalized.model_dump(mode="python")
        candidate["thesis_id"] = cls.canonical_id_for(candidate)
        return cls.model_validate(candidate)

    @property
    def rationale(self) -> str:
        return " ".join(
            item.text
            for item in (self.theme_rationale, self.catalyst_rationale, self.leader_rationale, self.flow_rationale)
            if item is not None
        )


class UsDayThesisChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    thesis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: ThesisChangeKind
    occurred_at: AwareDatetime
    note: str = Field(min_length=1, max_length=240)

    @field_validator("occurred_at")
    @classmethod
    def canonical_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_change(self, info: ValidationInfo) -> Self:
        identity_valid = bool(info.context and info.context.get("skip_identity")) or self.event_id == _content_id(
            self.model_dump(mode="python"), "event_id"
        )
        if self.note != self.note.strip() or not identity_valid:
            raise ValueError("invalid thesis change")
        return self

    @classmethod
    def create(cls, **payload: object) -> UsDayThesisChange:
        candidate = {"schema_version": 1, **payload, "event_id": "0" * 64}
        normalized = cls.model_validate(candidate, context={"skip_identity": True})
        candidate = normalized.model_dump(mode="python")
        candidate["event_id"] = _content_id(candidate, "event_id")
        return cls.model_validate(candidate)


def situation_id_for(situation: UsDaySituationMap) -> str:
    return hashlib.sha256(_canonical_json(situation.model_dump(mode="python")).encode()).hexdigest()


def _content_id(payload: Mapping[str, object], identity_field: str) -> str:
    body = dict(payload)
    body.pop(identity_field, None)
    return hashlib.sha256(_canonical_json(body).encode()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _canonical_refs(refs: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    by_id = {item.canonical_id: item for item in refs}
    return tuple(by_id[item] for item in sorted(by_id))


__all__ = (
    "DayTradeDecision",
    "EvidenceBoundRationale",
    "ThesisChangeKind",
    "TradeTarget",
    "UsDayChampion",
    "UsDayCurrentMarket",
    "UsDayPlaybook",
    "UsDayThesisChange",
    "UsDayTradeThesis",
    "situation_id_for",
)
