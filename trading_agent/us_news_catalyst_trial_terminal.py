from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_news_catalyst_trial_artifact import (
    cohorts_in,
    load_us_news_catalyst_setup_observation_manifest,
    outcomes_in,
    publish_us_news_catalyst_outcome,
    publish_us_news_catalyst_setup_observation_manifest,
)
from trading_agent.us_news_catalyst_trial_contract import (
    InvalidUsNewsCatalystTrialError,
    UsNewsCatalystTrialFinalizeResult,
    require_us_news_catalyst_trial,
)
from trading_agent.us_news_catalyst_trial_models import (
    InvalidUsNewsCatalystTrialModelError,
    UsNewsCatalystCohortArtifact,
    UsNewsCatalystCohortStatus,
)
from trading_agent.us_news_catalyst_trial_outcome_models import (
    US_NEWS_CATALYST_EVALUATOR_VERSION,
    US_NEWS_CATALYST_SETUP_HORIZON,
    UsNewsCatalystSetupObservationManifest,
    UsNewsCatalystTrialOutcomeArtifact,
    UsNewsCatalystTrialOutcomePayload,
    trial_outcome_artifact,
)

_MAX_TERMINAL_RECOVERY_DELAY = dt.timedelta(days=1)


def finalize_us_news_catalyst_trial(
    ledger: ExperimentLedgerStore,
    trial_id: str,
    cohort: UsNewsCatalystCohortArtifact,
    observation_manifest_path: Path | None,
    artifact_root: Path,
    *,
    finalized_at: dt.datetime,
) -> UsNewsCatalystTrialFinalizeResult:
    try:
        trial = require_us_news_catalyst_trial(
            ledger,
            trial_id,
            US_NEWS_CATALYST_EVALUATOR_VERSION,
        )
        events = ledger.multi_market_trial_events(trial_id)
        if not events or events[0].event.event_kind is not TrialEventKind.STARTED:
            raise InvalidUsNewsCatalystTrialError
        stored_cohort = _one_for_trial(cohorts_in(artifact_root), trial_id)
        if stored_cohort is None or stored_cohort != cohort:
            raise InvalidUsNewsCatalystTrialError
        terminal_at = finalized_at if len(events) == 1 else events[1].event.occurred_at
        _require_terminal_time(trial, cohort, terminal_at)
        manifest = _load_manifest(observation_manifest_path)
        outcome = _outcome(trial, cohort, manifest, terminal_at)
        existing = _one_for_trial(outcomes_in(artifact_root), trial_id)
        if existing is not None and existing != outcome:
            raise InvalidUsNewsCatalystTrialError
        if manifest is not None:
            _ = publish_us_news_catalyst_setup_observation_manifest(artifact_root, manifest)
        _, outcome_created = publish_us_news_catalyst_outcome(artifact_root, outcome)
        hashes = [cohort.artifact_id, outcome.artifact_id]
        if manifest is not None:
            hashes.append(manifest.manifest_id)
        event = ExperimentTrialEvent(
            trial_id=trial_id,
            sequence=2,
            event_kind=outcome.payload.terminal_kind,
            occurred_at=terminal_at,
            artifact_sha256s=tuple(sorted(hashes)),
            reason_codes=outcome.payload.reason_codes,
            previous_event_key=events[0].event_key,
        )
        with ledger.writer() as writer:
            event_created = writer.append_multi_market_trial_event(event)
        return UsNewsCatalystTrialFinalizeResult(outcome_created, event_created, outcome, event)
    except (AttributeError, InvalidUsNewsCatalystTrialModelError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystTrialError from None


def _load_manifest(path: Path | None) -> UsNewsCatalystSetupObservationManifest | None:
    return None if path is None else load_us_news_catalyst_setup_observation_manifest(path)


def _require_terminal_time(
    trial: MultiMarketExperimentTrialRegistration,
    cohort: UsNewsCatalystCohortArtifact,
    terminal_at: dt.datetime,
) -> None:
    bounds = regular_session_bounds(trial.planned_start)
    if (
        cohort.payload.trial_id != trial.trial_id
        or bounds is None
        or terminal_at < cohort.payload.observed_at + US_NEWS_CATALYST_SETUP_HORIZON
        or not bounds[0] <= terminal_at <= bounds[1] + _MAX_TERMINAL_RECOVERY_DELAY
    ):
        raise InvalidUsNewsCatalystTrialError


def _outcome(
    trial: MultiMarketExperimentTrialRegistration,
    cohort: UsNewsCatalystCohortArtifact,
    manifest: UsNewsCatalystSetupObservationManifest | None,
    terminal_at: dt.datetime,
) -> UsNewsCatalystTrialOutcomeArtifact:
    payload = cohort.payload
    if payload.status is UsNewsCatalystCohortStatus.INSUFFICIENT_CONTROL:
        return _censored(trial, cohort, terminal_at, "insufficient_zero_news_control")
    if manifest is None:
        return _censored(trial, cohort, terminal_at, "missing_setup_observations")
    _require_observations(cohort, manifest, terminal_at)
    by_symbol = {item.symbol: item for item in manifest.observations}
    treatment_confirmed = sum(by_symbol[symbol].setup_confirmed for symbol in payload.treatment_symbols)
    control_confirmed = sum(by_symbol[symbol].setup_confirmed for symbol in payload.control_symbols)
    treatment_bps = treatment_confirmed * 10_000 // len(payload.treatment_symbols)
    control_bps = control_confirmed * 10_000 // len(payload.control_symbols)
    return trial_outcome_artifact(
        UsNewsCatalystTrialOutcomePayload(
            trial_id=trial.trial_id,
            strategy_version=trial.strategy_version,
            session_date=trial.planned_start,
            cohort_artifact_id=cohort.artifact_id,
            observation_manifest_id=manifest.manifest_id,
            terminal_kind=TrialEventKind.COMPLETED,
            reason_codes=(),
            treatment_count=len(payload.treatment_symbols),
            control_count=len(payload.control_symbols),
            treatment_confirmed_count=treatment_confirmed,
            control_confirmed_count=control_confirmed,
            treatment_confirmation_bps=treatment_bps,
            control_confirmation_bps=control_bps,
            confirmation_lift_bps=treatment_bps - control_bps,
            terminal_at=terminal_at,
        )
    )


def _censored(
    trial: MultiMarketExperimentTrialRegistration,
    cohort: UsNewsCatalystCohortArtifact,
    terminal_at: dt.datetime,
    reason: str,
) -> UsNewsCatalystTrialOutcomeArtifact:
    payload = cohort.payload
    return trial_outcome_artifact(
        UsNewsCatalystTrialOutcomePayload(
            trial_id=trial.trial_id,
            strategy_version=trial.strategy_version,
            session_date=trial.planned_start,
            cohort_artifact_id=cohort.artifact_id,
            observation_manifest_id=None,
            terminal_kind=TrialEventKind.CENSORED,
            reason_codes=(reason,),
            treatment_count=len(payload.treatment_symbols),
            control_count=len(payload.control_symbols),
            treatment_confirmed_count=None,
            control_confirmed_count=None,
            treatment_confirmation_bps=None,
            control_confirmation_bps=None,
            confirmation_lift_bps=None,
            terminal_at=terminal_at,
        )
    )


def _require_observations(
    cohort: UsNewsCatalystCohortArtifact,
    manifest: UsNewsCatalystSetupObservationManifest,
    terminal_at: dt.datetime,
) -> None:
    payload = cohort.payload
    expected = tuple(sorted((*payload.treatment_symbols, *payload.control_symbols)))
    actual = tuple(item.symbol for item in manifest.observations)
    if (
        manifest.trial_id != payload.trial_id
        or manifest.cohort_artifact_id != cohort.artifact_id
        or manifest.evaluator_version != US_NEWS_CATALYST_EVALUATOR_VERSION
        or actual != expected
        or any(
            item.observed_at < payload.observed_at + US_NEWS_CATALYST_SETUP_HORIZON
            or item.observed_at > terminal_at
            for item in manifest.observations
        )
    ):
        raise InvalidUsNewsCatalystTrialError


def _one_for_trial(
    values: tuple[UsNewsCatalystCohortArtifact, ...] | tuple[UsNewsCatalystTrialOutcomeArtifact, ...],
    trial_id: str,
) -> UsNewsCatalystCohortArtifact | UsNewsCatalystTrialOutcomeArtifact | None:
    matches = tuple(item for item in values if item.payload.trial_id == trial_id)
    if len(matches) > 1:
        raise InvalidUsNewsCatalystTrialError
    return None if not matches else matches[0]


__all__ = ("finalize_us_news_catalyst_trial",)
