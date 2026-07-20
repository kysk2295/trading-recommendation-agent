from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from pydantic import ValidationError

from trading_agent.alpaca_news_opportunity_evidence import AlpacaNewsOpportunityEvidenceBundle
from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_news_catalyst_opportunity_models import (
    UsNewsCatalystOpportunityProjection,
    UsNewsCatalystProjectionStatus,
)
from trading_agent.us_news_catalyst_trial_artifact import cohorts_in, publish_us_news_catalyst_cohort
from trading_agent.us_news_catalyst_trial_contract import (
    InvalidUsNewsCatalystTrialError,
    UsNewsCatalystTrialStartResult,
    require_us_news_catalyst_trial,
)
from trading_agent.us_news_catalyst_trial_models import (
    InvalidUsNewsCatalystTrialModelError,
    UsNewsCatalystCohortArtifact,
    UsNewsCatalystCohortPayload,
    UsNewsCatalystCohortStatus,
    cohort_artifact,
)
from trading_agent.us_news_catalyst_trial_outcome_models import US_NEWS_CATALYST_EVALUATOR_VERSION


def start_us_news_catalyst_trial(
    ledger: ExperimentLedgerStore,
    trial_id: str,
    projection: UsNewsCatalystOpportunityProjection,
    evidence: AlpacaNewsOpportunityEvidenceBundle,
    artifact_root: Path,
    *,
    started_at: dt.datetime,
) -> UsNewsCatalystTrialStartResult:
    try:
        trial = require_us_news_catalyst_trial(
            ledger,
            trial_id,
            US_NEWS_CATALYST_EVALUATOR_VERSION,
        )
        events = ledger.multi_market_trial_events(trial_id)
        event_time = started_at if not events else events[0].event.occurred_at
        _require_start_source(trial, projection, evidence, event_time)
        cohort = cohort_artifact(_cohort_payload(trial, projection, evidence))
        existing = _one_for_trial(cohorts_in(artifact_root), trial_id)
        if existing is not None and existing != cohort:
            raise InvalidUsNewsCatalystTrialError
        _, cohort_created = publish_us_news_catalyst_cohort(artifact_root, cohort)
        event = ExperimentTrialEvent(
            trial_id=trial_id,
            sequence=1,
            event_kind=TrialEventKind.STARTED,
            occurred_at=event_time,
            artifact_sha256s=(),
            reason_codes=(),
            previous_event_key=None,
        )
        with ledger.writer() as writer:
            event_created = writer.append_multi_market_trial_event(event)
        return UsNewsCatalystTrialStartResult(cohort_created, event_created, cohort, event)
    except (AttributeError, InvalidUsNewsCatalystTrialModelError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystTrialError from None


def _require_start_source(
    trial: MultiMarketExperimentTrialRegistration,
    projection: UsNewsCatalystOpportunityProjection,
    evidence: AlpacaNewsOpportunityEvidenceBundle,
    started_at: dt.datetime,
) -> None:
    bounds = regular_session_bounds(trial.planned_start)
    snapshot = projection.snapshot
    if (
        bounds is None
        or projection.status is not UsNewsCatalystProjectionStatus.RANKED
        or snapshot is None
        or projection.strategy_version != trial.strategy_version
        or projection.evidence_bundle_id != evidence.bundle_id
        or projection.projected_at.astimezone(NEW_YORK).date() != trial.planned_start
        or not bounds[0] <= started_at < bounds[1]
        or not projection.projected_at <= started_at < snapshot.valid_until
    ):
        raise InvalidUsNewsCatalystTrialError


def _cohort_payload(
    trial: MultiMarketExperimentTrialRegistration,
    projection: UsNewsCatalystOpportunityProjection,
    evidence: AlpacaNewsOpportunityEvidenceBundle,
) -> UsNewsCatalystCohortPayload:
    snapshot = projection.snapshot
    if snapshot is None:
        raise InvalidUsNewsCatalystTrialError
    treatment = tuple(item.symbol for item in snapshot.candidates)
    available = tuple(
        item.symbol
        for item in evidence.snapshots
        if not item.observations and item.symbol not in treatment
    )
    ordered = tuple(
        sorted(
            available,
            key=lambda symbol: hashlib.sha256(f"{trial.trial_id}|{symbol}".encode()).hexdigest(),
        )
    )
    control = ordered[: len(treatment)]
    status = (
        UsNewsCatalystCohortStatus.READY
        if len(control) == len(treatment)
        else UsNewsCatalystCohortStatus.INSUFFICIENT_CONTROL
    )
    return UsNewsCatalystCohortPayload(
        trial_id=trial.trial_id,
        strategy_version=trial.strategy_version,
        session_date=trial.planned_start,
        projection_id=projection.projection_id,
        evidence_bundle_id=evidence.bundle_id,
        opportunity_id=snapshot.opportunity_id,
        observed_at=projection.projected_at,
        treatment_symbols=treatment,
        control_symbols=control,
        status=status,
    )


def _one_for_trial(
    values: tuple[UsNewsCatalystCohortArtifact, ...],
    trial_id: str,
) -> UsNewsCatalystCohortArtifact | None:
    matches = tuple(item for item in values if item.payload.trial_id == trial_id)
    if len(matches) > 1:
        raise InvalidUsNewsCatalystTrialError
    return None if not matches else matches[0]


__all__ = ("start_us_news_catalyst_trial",)
