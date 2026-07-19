from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_models import TrialKind
from trading_agent.experiment_scope_models import ExperimentScopeKind
from trading_agent.multi_market_experiment_models import (
    MultiMarketExperimentScope,
    multi_market_experiment_scope_key,
)
from trading_agent.research_identity_models import MarketId, StrategyLaneRef

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_KST = ZoneInfo("Asia/Seoul")
_NEW_YORK = ZoneInfo("America/New_York")


class MultiMarketExperimentTrialRegistration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    trial_id: str
    strategy_version: str
    trial_kind: TrialKind
    experiment_scope: MultiMarketExperimentScope
    experiment_scope_key: str
    strategy_lane: StrategyLaneRef
    evaluator_version: str
    data_version: str
    feed_entitlement: str
    planned_start: dt.date
    planned_end: dt.date
    registered_at: dt.datetime
    evidence_budget: tuple[str, ...]

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        identities = (self.trial_id, self.strategy_version, self.evaluator_version)
        session_open = market_session_open(self.strategy_lane.market_id, self.planned_start)
        if (
            not all(_IDENTIFIER.fullmatch(value) for value in identities)
            or _HEX64.fullmatch(self.data_version) is None
            or not _canonical_text(self.feed_entitlement)
            or self.experiment_scope_key != multi_market_experiment_scope_key(self.experiment_scope)
            or self.experiment_scope.scope_kind is not ExperimentScopeKind.SINGLE_LANE
            or self.experiment_scope.primary_lane != self.strategy_lane
            or self.experiment_scope.lanes != (self.strategy_lane,)
            or self.trial_kind is not TrialKind.SHADOW_FORWARD
            or not _aware(self.registered_at)
            or self.planned_start.weekday() >= 5
            or self.planned_end.weekday() >= 5
            or self.planned_end < self.planned_start
            or self.registered_at >= session_open
            or not _canonical_set(self.evidence_budget)
        ):
            raise ValueError("invalid multi-market experiment trial registration")
        return self


def market_local_date(market_id: MarketId, value: dt.datetime) -> dt.date:
    return value.astimezone(_market_zone(market_id)).date()


def market_session_open(market_id: MarketId, session_date: dt.date) -> dt.datetime:
    zone = _market_zone(market_id)
    opening = dt.time(9, 30) if market_id is MarketId.US_EQUITIES else dt.time(9)
    return dt.datetime.combine(session_date, opening, zone)


def _market_zone(market_id: MarketId) -> ZoneInfo:
    if market_id is MarketId.US_EQUITIES:
        return _NEW_YORK
    if market_id is MarketId.KR_EQUITIES:
        return _KST
    raise ValueError("unsupported market")


def _canonical_set(values: tuple[str, ...]) -> bool:
    return bool(values) and values == tuple(sorted(set(values))) and all(_canonical_text(value) for value in values)


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(character in value for character in "\r\n\t")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
