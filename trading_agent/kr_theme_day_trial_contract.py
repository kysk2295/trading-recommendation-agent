from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Final, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_models import TrialKind
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kr_theme_day_composite import KrThemeDayCompositeAuthority
from trading_agent.kr_theme_day_composite_evidence import kr_theme_day_composite_evidence
from trading_agent.kr_theme_day_trial_calendar import (
    kr_theme_day_trial_evidence_budget,
    require_kr_theme_day_trial_calendar,
)
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE
from trading_agent.multi_market_experiment_models import MultiMarketHypothesisRegistration
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration

_EVALUATOR_VERSION: Final = "kr-theme-day-forward-v1"
_FEED_ENTITLEMENT: Final = "KIS_read_only_domestic_quotes"
_BASE_EVIDENCE_BUDGET: Final = (
    "cost_model:entry_ask_plus_20bps",
    "counterfactual:no_entry",
    "maximum_missing_evidence_rate:0",
    "minimum_completed_signals:30",
    "minimum_forward_sessions:20",
    "review_gates:fillability_drawdown_stability_multiple_testing",
)


class InvalidKrThemeDayTrialError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day shadow trial input is invalid"


class KrThemeDayTrialRegistrationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    code_version: str
    session_date: dt.date
    registered_at: dt.datetime
    calendar_snapshot: KrSessionCalendarSnapshot
    opportunity_strategy_version: str

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        values = (self.strategy_version, self.code_version, self.opportunity_strategy_version)
        if not _aware(self.registered_at) or any(not value or value != value.strip() for value in values):
            raise InvalidKrThemeDayTrialError
        _ = require_kr_theme_day_trial_calendar(
            self.calendar_snapshot,
            self.session_date,
            self.registered_at,
        )
        return self


@dataclass(frozen=True, slots=True)
class KrThemeDayTrialRegistrationIdentity:
    strategy_version: str
    code_version: str
    session_date: dt.date
    registered_at: dt.datetime
    calendar_snapshot_id: str
    composite_authority: KrThemeDayCompositeAuthority


def kr_theme_day_trial_id(session_date: dt.date, strategy_version: str) -> str:
    if not strategy_version or strategy_version != strategy_version.strip():
        raise InvalidKrThemeDayTrialError
    digest = hashlib.sha256(strategy_version.encode()).hexdigest()[:16]
    return f"trial-kr-theme-vwap-{session_date:%Y%m%d}-{digest}"


def kr_theme_day_trial_identity(
    request: KrThemeDayTrialRegistrationRequest,
    composite: KrThemeDayCompositeAuthority,
) -> KrThemeDayTrialRegistrationIdentity:
    return KrThemeDayTrialRegistrationIdentity(
        strategy_version=request.strategy_version,
        code_version=request.code_version,
        session_date=request.session_date,
        registered_at=request.registered_at,
        calendar_snapshot_id=require_kr_theme_day_trial_calendar(
            request.calendar_snapshot,
            request.session_date,
            request.registered_at,
        ),
        composite_authority=composite,
    )


def build_kr_theme_day_trial_registration(
    identity: KrThemeDayTrialRegistrationIdentity,
    hypothesis: MultiMarketHypothesisRegistration,
) -> MultiMarketExperimentTrialRegistration:
    evidence_budget = kr_theme_day_trial_evidence_budget(
        (*_BASE_EVIDENCE_BUDGET, *kr_theme_day_composite_evidence(identity.composite_authority)),
        identity.calendar_snapshot_id,
    )
    return MultiMarketExperimentTrialRegistration(
        trial_id=kr_theme_day_trial_id(identity.session_date, identity.strategy_version),
        strategy_version=identity.strategy_version,
        trial_kind=TrialKind.SHADOW_FORWARD,
        experiment_scope=hypothesis.experiment_scope,
        experiment_scope_key=hypothesis.experiment_scope_key,
        strategy_lane=KR_THEME_LEADER_VWAP_RECLAIM_LANE,
        evaluator_version=_EVALUATOR_VERSION,
        data_version=_data_version(identity, evidence_budget),
        feed_entitlement=_FEED_ENTITLEMENT,
        planned_start=identity.session_date,
        planned_end=identity.session_date,
        registered_at=identity.registered_at,
        evidence_budget=evidence_budget,
    )


def _data_version(
    identity: KrThemeDayTrialRegistrationIdentity,
    evidence_budget: tuple[str, ...],
) -> str:
    material = "|".join(
        (
            _EVALUATOR_VERSION,
            identity.strategy_version,
            identity.code_version,
            identity.session_date.isoformat(),
            identity.calendar_snapshot_id,
            _FEED_ENTITLEMENT,
            *evidence_budget,
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
