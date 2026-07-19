from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Final, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind, TrialKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE
from trading_agent.kr_theme_research_registration import (
    InvalidKrThemeResearchRegistrationError,
    KrThemeProjectionAuthorityRequest,
    kr_theme_day_strategy_version,
    require_registered_kr_theme_day_strategy,
)
from trading_agent.multi_market_experiment_models import MultiMarketHypothesisRegistration
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration

_EVALUATOR_VERSION: Final = "kr-theme-day-forward-v1"
_FEED_ENTITLEMENT: Final = "KIS_read_only_domestic_quotes"
_EVIDENCE_BUDGET: Final = (
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

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            not self.strategy_version
            or not self.code_version
            or self.strategy_version != self.strategy_version.strip()
            or self.code_version != self.code_version.strip()
            or not _aware(self.registered_at)
        ):
            raise InvalidKrThemeDayTrialError
        return self


@dataclass(frozen=True, slots=True)
class KrThemeDayTrialRegistrationResult:
    created: bool
    registration: MultiMarketExperimentTrialRegistration


@dataclass(frozen=True, slots=True)
class KrThemeDayTrialEventResult:
    created: bool
    event: ExperimentTrialEvent


def kr_theme_day_trial_id(session_date: dt.date, strategy_version: str) -> str:
    if not strategy_version or strategy_version != strategy_version.strip():
        raise InvalidKrThemeDayTrialError
    digest = hashlib.sha256(strategy_version.encode()).hexdigest()[:16]
    return f"trial-kr-theme-vwap-{session_date:%Y%m%d}-{digest}"


def register_kr_theme_day_shadow_trial(
    ledger: ExperimentLedgerStore,
    request: KrThemeDayTrialRegistrationRequest,
) -> KrThemeDayTrialRegistrationResult:
    try:
        request = KrThemeDayTrialRegistrationRequest.model_validate(request.model_dump(mode="python"))
        version = require_registered_kr_theme_day_strategy(
            ledger,
            KrThemeProjectionAuthorityRequest(
                strategy_version=request.strategy_version,
                code_version=request.code_version,
                projected_at=request.registered_at,
            ),
        )
        hypothesis = _hypothesis(ledger, version.hypothesis_id, version.experiment_scope_key)
        registration = _registration(request, hypothesis)
        with ledger.writer() as writer:
            created = writer.register_multi_market_trial(registration)
    except (
        AttributeError,
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        InvalidKrThemeResearchRegistrationError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrThemeDayTrialError from None
    return KrThemeDayTrialRegistrationResult(created, registration)


def start_kr_theme_day_shadow_trial(
    ledger: ExperimentLedgerStore,
    trial_id: str,
    occurred_at: dt.datetime,
) -> KrThemeDayTrialEventResult:
    try:
        matches = tuple(
            item.registration for item in ledger.multi_market_trials() if item.registration.trial_id == trial_id
        )
        if len(matches) != 1:
            raise InvalidKrThemeDayTrialError
        _require_exact_day_trial(ledger, matches[0])
        event = ExperimentTrialEvent(
            trial_id=trial_id,
            sequence=1,
            event_kind=TrialEventKind.STARTED,
            occurred_at=occurred_at,
            artifact_sha256s=(),
            reason_codes=(),
            previous_event_key=None,
        )
        with ledger.writer() as writer:
            created = writer.append_multi_market_trial_event(event)
    except (
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrThemeDayTrialError from None
    return KrThemeDayTrialEventResult(created, event)


def _hypothesis(
    ledger: ExperimentLedgerStore,
    hypothesis_id: str,
    scope_key: str,
) -> MultiMarketHypothesisRegistration:
    matches = tuple(
        item.registration
        for item in ledger.multi_market_hypotheses()
        if item.registration.hypothesis_id == hypothesis_id and item.registration.experiment_scope_key == scope_key
    )
    if len(matches) != 1:
        raise InvalidKrThemeDayTrialError
    return matches[0]


def _registration(
    request: KrThemeDayTrialRegistrationRequest,
    hypothesis: MultiMarketHypothesisRegistration,
) -> MultiMarketExperimentTrialRegistration:
    return MultiMarketExperimentTrialRegistration(
        trial_id=kr_theme_day_trial_id(request.session_date, request.strategy_version),
        strategy_version=request.strategy_version,
        trial_kind=TrialKind.SHADOW_FORWARD,
        experiment_scope=hypothesis.experiment_scope,
        experiment_scope_key=hypothesis.experiment_scope_key,
        strategy_lane=KR_THEME_LEADER_VWAP_RECLAIM_LANE,
        evaluator_version=_EVALUATOR_VERSION,
        data_version=_data_version(request),
        feed_entitlement=_FEED_ENTITLEMENT,
        planned_start=request.session_date,
        planned_end=request.session_date,
        registered_at=request.registered_at,
        evidence_budget=_EVIDENCE_BUDGET,
    )


def _require_exact_day_trial(
    ledger: ExperimentLedgerStore,
    trial: MultiMarketExperimentTrialRegistration,
) -> None:
    versions = tuple(
        item.registration
        for item in ledger.multi_market_strategy_versions()
        if item.registration.strategy_version == trial.strategy_version
    )
    if len(versions) != 1:
        raise InvalidKrThemeDayTrialError
    version = versions[0]
    if (
        trial.strategy_lane != KR_THEME_LEADER_VWAP_RECLAIM_LANE
        or version.strategy_lane != KR_THEME_LEADER_VWAP_RECLAIM_LANE
        or version.strategy_version != kr_theme_day_strategy_version(version.code_version)
    ):
        raise InvalidKrThemeDayTrialError
    hypothesis = _hypothesis(ledger, version.hypothesis_id, version.experiment_scope_key)
    expected = _registration(
        KrThemeDayTrialRegistrationRequest(
            strategy_version=version.strategy_version,
            code_version=version.code_version,
            session_date=trial.planned_start,
            registered_at=trial.registered_at,
        ),
        hypothesis,
    )
    if trial != expected:
        raise InvalidKrThemeDayTrialError


def _data_version(request: KrThemeDayTrialRegistrationRequest) -> str:
    material = "|".join(
        (
            _EVALUATOR_VERSION,
            request.strategy_version,
            request.code_version,
            request.session_date.isoformat(),
            _FEED_ENTITLEMENT,
            *_EVIDENCE_BUDGET,
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
