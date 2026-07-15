from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_paper_contract import ALPACA_PAPER_TRADING_URL
from trading_agent.lane_policy_models import (
    LaneExecutionPolicy,
    LaneId,
    LaneOrderAuthority,
    LaneRiskContract,
    LaneRiskEnforcement,
)
from trading_agent.us_equity_calendar import regular_session_bounds

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9_./-]{0,127}$")


class InvalidLaneContractError(ValueError):
    @override
    def __str__(self) -> str:
        return "lane control-plane 계약이 승인된 격리·인과성 경계와 일치하지 않습니다"


class LaneAccountBindingMode(StrEnum):
    DEDICATED_PAPER = "dedicated_paper"
    FORBIDDEN = "forbidden"


class ExperimentScopeKind(StrEnum):
    SINGLE_LANE = "single_lane"
    CROSS_LANE_HYPOTHESIS = "cross_lane_hypothesis"


class LaneManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    lane_id: LaneId
    manifest_version: str
    registered_at: dt.datetime
    ledger_namespace: str
    strategy_ids: tuple[str, ...]
    account_binding_mode: LaneAccountBindingMode
    execution_policy: LaneExecutionPolicy
    risk_contract: LaneRiskContract

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        authority = self.execution_policy.order_authority
        enforcement = self.risk_contract.enforcement
        expected_binding = (
            LaneAccountBindingMode.DEDICATED_PAPER
            if authority is LaneOrderAuthority.ALPACA_PAPER
            else LaneAccountBindingMode.FORBIDDEN
        )
        expected_enforcement = {
            LaneOrderAuthority.ALPACA_PAPER: LaneRiskEnforcement.BROKER_PAPER,
            LaneOrderAuthority.SHADOW_ONLY: LaneRiskEnforcement.SHADOW,
            LaneOrderAuthority.NONE: LaneRiskEnforcement.NONE,
        }[authority]
        if (
            self.execution_policy.lane_id is not self.lane_id
            or self.account_binding_mode is not expected_binding
            or enforcement is not expected_enforcement
            or not _aware(self.registered_at)
            or not _IDENTIFIER.fullmatch(self.manifest_version)
            or not _NAMESPACE.fullmatch(self.ledger_namespace)
            or ".." in self.ledger_namespace
            or "//" in self.ledger_namespace
            or not self.strategy_ids
            or self.strategy_ids != tuple(sorted(set(self.strategy_ids)))
            or not all(_IDENTIFIER.fullmatch(strategy_id) for strategy_id in self.strategy_ids)
        ):
            raise ValueError("invalid lane manifest")
        return self


class LaneAccountBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    lane_id: LaneId
    account_fingerprint: str = Field(repr=False)
    execution_ledger_fingerprint: str = Field(repr=False)
    paper_base_url: Literal["https://paper-api.alpaca.markets"] = ALPACA_PAPER_TRADING_URL
    bound_at: dt.datetime

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if (
            not _HEX64.fullmatch(self.account_fingerprint)
            or not _HEX64.fullmatch(self.execution_ledger_fingerprint)
            or not _aware(self.bound_at)
        ):
            raise ValueError("invalid lane account binding")
        return self


class ExperimentScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    scope_kind: ExperimentScopeKind
    hypothesis_id: str
    primary_lane: LaneId
    lanes: tuple[LaneId, ...]
    source_hypothesis_ids: tuple[str, ...] = ()
    combination_rule: str | None = None
    registered_at: dt.datetime

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        lanes = tuple(sorted(set(self.lanes), key=str))
        sources = tuple(sorted(set(self.source_hypothesis_ids)))
        text_valid = _IDENTIFIER.fullmatch(self.hypothesis_id) is not None and all(
            _IDENTIFIER.fullmatch(source) for source in sources
        )
        if (
            not _aware(self.registered_at)
            or not text_valid
            or self.lanes != lanes
            or self.source_hypothesis_ids != sources
            or self.primary_lane not in lanes
        ):
            raise ValueError("invalid experiment scope identity")
        if self.scope_kind is ExperimentScopeKind.SINGLE_LANE:
            if lanes != (self.primary_lane,) or sources or self.combination_rule is not None:
                raise ValueError("single-lane scope cannot mix results")
            return self
        if (
            len(lanes) < 2
            or len(sources) < 2
            or self.hypothesis_id in sources
            or self.combination_rule is None
            or not self.combination_rule.strip()
            or self.combination_rule != self.combination_rule.strip()
        ):
            raise ValueError("cross-lane scope requires a new pre-registered hypothesis")
        return self


class LaneDailySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    lane_id: LaneId
    session_date: dt.date
    finalized_at: dt.datetime
    manifest_key: str
    experiment_scope_keys: tuple[str, ...]
    source_ledger_generation: int
    source_ledger_sha256: str
    champion_strategy_versions: tuple[str, ...]
    data_quality_complete: bool
    allocation_eligible: bool
    incidents: tuple[str, ...]
    conservative_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    planned_open_risk: Decimal
    open_order_count: int
    open_position_count: int

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        money = (
            self.conservative_equity,
            self.realized_pnl,
            self.unrealized_pnl,
            self.planned_open_risk,
        )
        if (
            not _aware(self.finalized_at)
            or not _HEX64.fullmatch(self.manifest_key)
            or not self.experiment_scope_keys
            or self.experiment_scope_keys != tuple(sorted(set(self.experiment_scope_keys)))
            or not all(_HEX64.fullmatch(key) for key in self.experiment_scope_keys)
            or self.source_ledger_generation < 0
            or not _HEX64.fullmatch(self.source_ledger_sha256)
            or not all(value.is_finite() for value in money)
            or self.conservative_equity < 0
            or self.planned_open_risk < 0
            or self.open_order_count < 0
            or self.open_position_count < 0
            or self.champion_strategy_versions != tuple(sorted(set(self.champion_strategy_versions)))
            or not all(_IDENTIFIER.fullmatch(version) for version in self.champion_strategy_versions)
            or self.incidents != tuple(sorted(set(self.incidents)))
            or not all(incident and incident.strip() == incident for incident in self.incidents)
        ):
            raise ValueError("invalid finalized lane snapshot")
        if self.lane_id is LaneId.INTRADAY_MOMENTUM and (
            self.open_order_count != 0 or self.open_position_count != 0 or self.planned_open_risk != 0
        ):
            raise ValueError("intraday final snapshot must be flat")
        if self.lane_id is LaneId.MARKET_REGIME and (
            any(value != 0 for value in money) or self.open_order_count != 0 or self.open_position_count != 0
        ):
            raise ValueError("signal-only regime snapshot cannot contain broker exposure")
        if self.allocation_eligible and (
            not self.data_quality_complete or bool(self.incidents) or not self.champion_strategy_versions
        ):
            raise ValueError("allocation eligibility requires clean data and a champion")
        return self


def lane_account_binding(
    manifest: LaneManifest,
    account_fingerprint: str,
    execution_ledger_fingerprint: str,
    bound_at: dt.datetime,
) -> LaneAccountBinding:
    if (
        manifest.account_binding_mode is not LaneAccountBindingMode.DEDICATED_PAPER
        or manifest.execution_policy.order_authority is not LaneOrderAuthority.ALPACA_PAPER
        or manifest.risk_contract.enforcement is not LaneRiskEnforcement.BROKER_PAPER
    ):
        raise InvalidLaneContractError
    return LaneAccountBinding(
        lane_id=manifest.lane_id,
        account_fingerprint=account_fingerprint,
        execution_ledger_fingerprint=execution_ledger_fingerprint,
        bound_at=bound_at,
    )


def single_lane_experiment_scope(
    lane_id: LaneId,
    hypothesis_id: str,
    registered_at: dt.datetime,
) -> ExperimentScope:
    return ExperimentScope(
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id=hypothesis_id,
        primary_lane=lane_id,
        lanes=(lane_id,),
        registered_at=registered_at,
    )


def require_scope_registered_before_session(
    scope: ExperimentScope,
    session_date: dt.date,
) -> None:
    bounds = regular_session_bounds(session_date)
    if bounds is None or scope.registered_at.astimezone(dt.UTC) >= bounds[0].astimezone(dt.UTC):
        raise InvalidLaneContractError


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
