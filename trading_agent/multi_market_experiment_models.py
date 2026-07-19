from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_scope_models import ExperimentScopeKind
from trading_agent.research_identity_models import (
    AgentFamily,
    AgentOperatingMode,
    MarketId,
    StrategyLaneRef,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class InvalidMultiMarketExperimentModelError(ValueError):
    def __str__(self) -> str:
        return "invalid multi-market experiment model"


class MultiMarketExperimentScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    scope_kind: ExperimentScopeKind
    hypothesis_id: str
    primary_lane: StrategyLaneRef
    lanes: tuple[StrategyLaneRef, ...]
    source_hypothesis_ids: tuple[str, ...] = ()
    combination_rule: str | None = None
    registered_at: dt.datetime

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        lane_ids = tuple(lane.canonical_id for lane in self.lanes)
        sources = tuple(sorted(set(self.source_hypothesis_ids)))
        if (
            _IDENTIFIER.fullmatch(self.hypothesis_id) is None
            or not _aware(self.registered_at)
            or lane_ids != tuple(sorted(set(lane_ids)))
            or self.primary_lane.canonical_id not in lane_ids
            or any(lane.market_id is not self.primary_lane.market_id for lane in self.lanes)
            or sources != self.source_hypothesis_ids
            or not all(_IDENTIFIER.fullmatch(source) for source in sources)
        ):
            raise InvalidMultiMarketExperimentModelError
        match self.scope_kind:
            case ExperimentScopeKind.SINGLE_LANE:
                if self.lanes != (self.primary_lane,) or sources or self.combination_rule is not None:
                    raise InvalidMultiMarketExperimentModelError
            case ExperimentScopeKind.CROSS_LANE_HYPOTHESIS:
                if (
                    len(self.lanes) < 2
                    or len(sources) < 2
                    or self.hypothesis_id in sources
                    or self.combination_rule is None
                    or not _canonical_text(self.combination_rule)
                ):
                    raise InvalidMultiMarketExperimentModelError
            case unreachable:
                assert_never(unreachable)
        return self


class MultiMarketHypothesisRegistration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    hypothesis_id: str
    experiment_scope: MultiMarketExperimentScope
    experiment_scope_key: str
    hypothesis: str
    falsification_rule: str
    source_registered_at: dt.datetime
    ledger_recorded_at: dt.datetime

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        if (
            self.hypothesis_id != self.experiment_scope.hypothesis_id
            or self.experiment_scope_key != multi_market_experiment_scope_key(self.experiment_scope)
            or not _canonical_text(self.hypothesis)
            or not _canonical_text(self.falsification_rule)
            or not _aware(self.source_registered_at)
            or not _aware(self.ledger_recorded_at)
            or self.source_registered_at != self.experiment_scope.registered_at
            or self.ledger_recorded_at < self.source_registered_at
        ):
            raise InvalidMultiMarketExperimentModelError
        return self


class MultiMarketStrategyVersionRegistration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_version: str
    hypothesis_id: str
    experiment_scope_key: str
    strategy_lane: StrategyLaneRef
    operating_mode: AgentOperatingMode
    code_version: str
    parameter_set: tuple[str, ...]
    data_contract: tuple[str, ...]
    cost_model: tuple[str, ...]
    portfolio_policy: tuple[str, ...]
    source_registered_at: dt.datetime
    ledger_recorded_at: dt.datetime

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        identities = (self.strategy_version, self.hypothesis_id, self.code_version)
        contracts = (
            self.parameter_set,
            self.data_contract,
            self.cost_model,
            self.portfolio_policy,
        )
        if (
            not all(_IDENTIFIER.fullmatch(value) for value in identities)
            or _HEX64.fullmatch(self.experiment_scope_key) is None
            or not all(_ordered_contract(values) for values in contracts)
            or not _aware(self.source_registered_at)
            or not _aware(self.ledger_recorded_at)
            or self.ledger_recorded_at < self.source_registered_at
            or not _operating_mode_allowed(self.strategy_lane, self.operating_mode)
        ):
            raise InvalidMultiMarketExperimentModelError
        return self


def multi_market_experiment_scope_key(scope: MultiMarketExperimentScope) -> str:
    payload = json.dumps(
        scope.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _operating_mode_allowed(
    lane: StrategyLaneRef,
    operating_mode: AgentOperatingMode,
) -> bool:
    match operating_mode:
        case AgentOperatingMode.CONTRACT_ONLY | AgentOperatingMode.SHADOW:
            return True
        case AgentOperatingMode.ALPACA_PAPER:
            return lane.market_id is MarketId.US_EQUITIES and lane.agent_family in {
                AgentFamily.DAY_TRADING,
                AgentFamily.SWING_TRADING,
            }
        case unreachable:
            assert_never(unreachable)


def _ordered_contract(values: tuple[str, ...]) -> bool:
    return bool(values) and len(values) == len(set(values)) and all(_canonical_text(value) for value in values)


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
