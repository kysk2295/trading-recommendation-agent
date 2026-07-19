from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pydantic import ValidationError

from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerReader,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.kr_theme_day_composite import (
    KrThemeDayCompositeAuthorityRequest,
    require_exact_kr_theme_day_composite,
)
from trading_agent.kr_theme_day_composite_evidence import (
    KrThemeDayCompositeEvidenceRequest,
    require_exact_kr_theme_day_composite_evidence,
)
from trading_agent.kr_theme_day_trial_calendar import calendar_snapshot_id_from_evidence
from trading_agent.kr_theme_day_trial_contract import (
    InvalidKrThemeDayTrialError,
    KrThemeDayTrialRegistrationIdentity,
    KrThemeDayTrialRegistrationRequest,
    build_kr_theme_day_trial_registration,
    kr_theme_day_trial_id,
    kr_theme_day_trial_identity,
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

__all__ = (
    "InvalidKrThemeDayTrialError",
    "KrThemeDayTrialRegistrationRequest",
    "kr_theme_day_trial_id",
)


@dataclass(frozen=True, slots=True)
class KrThemeDayTrialRegistrationResult:
    created: bool
    registration: MultiMarketExperimentTrialRegistration


@dataclass(frozen=True, slots=True)
class KrThemeDayTrialEventResult:
    created: bool
    event: ExperimentTrialEvent


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
        composite = require_exact_kr_theme_day_composite(
            ledger,
            KrThemeDayCompositeAuthorityRequest(
                day_strategy_version=request.strategy_version,
                opportunity_strategy_version=request.opportunity_strategy_version,
                as_of=request.registered_at,
            ),
        )
        registration = build_kr_theme_day_trial_registration(
            kr_theme_day_trial_identity(request, composite),
            hypothesis,
        )
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
        require_exact_kr_theme_day_trial(ledger, matches[0])
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
    ledger: ExperimentLedgerReader,
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


def require_exact_kr_theme_day_trial(
    ledger: ExperimentLedgerReader,
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
    composite = require_exact_kr_theme_day_composite_evidence(
        ledger,
        KrThemeDayCompositeEvidenceRequest(
            day_strategy_version=version.strategy_version,
            evidence_budget=trial.evidence_budget,
            as_of=trial.registered_at,
        ),
    )
    expected = build_kr_theme_day_trial_registration(
        KrThemeDayTrialRegistrationIdentity(
            strategy_version=version.strategy_version,
            code_version=version.code_version,
            session_date=trial.planned_start,
            registered_at=trial.registered_at,
            calendar_snapshot_id=calendar_snapshot_id_from_evidence(trial.evidence_budget),
            composite_authority=composite,
        ),
        hypothesis,
    )
    if trial != expected:
        raise InvalidKrThemeDayTrialError
