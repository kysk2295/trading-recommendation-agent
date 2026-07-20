from __future__ import annotations

from dataclasses import dataclass, replace

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration
from trading_agent.multi_market_trial_store import StoredMultiMarketTrialEvent
from trading_agent.us_news_catalyst_trial_models import UsNewsCatalystCohortArtifact
from trading_agent.us_news_catalyst_trial_outcome_models import (
    UsNewsCatalystSetupObservationManifest,
    UsNewsCatalystTrialOutcomeArtifact,
)


@dataclass(frozen=True, slots=True)
class UsNewsCatalystTrialAggregate:
    completed: int = 0
    censored: int = 0
    failed: int = 0
    missing: int = 0
    treatment_count: int = 0
    control_count: int = 0
    treatment_confirmed: int = 0
    control_confirmed: int = 0


def aggregate_us_news_catalyst_trials(
    ledger: ExperimentLedgerReader,
    trials: tuple[MultiMarketExperimentTrialRegistration, ...],
    cohorts: tuple[UsNewsCatalystCohortArtifact, ...],
    manifests: tuple[UsNewsCatalystSetupObservationManifest, ...],
    outcomes: tuple[UsNewsCatalystTrialOutcomeArtifact, ...],
) -> UsNewsCatalystTrialAggregate:
    cohort_by_trial = _cohort_map(cohorts)
    manifest_by_trial = _manifest_map(manifests)
    outcome_by_trial = _outcome_map(outcomes)
    aggregate = UsNewsCatalystTrialAggregate()
    for trial in trials:
        events = ledger.multi_market_trial_events(trial.trial_id)
        if len(events) != 2:
            aggregate = replace(aggregate, missing=aggregate.missing + 1)
            continue
        cohort = cohort_by_trial.get(trial.trial_id)
        manifest = manifest_by_trial.get(trial.trial_id)
        outcome = outcome_by_trial.get(trial.trial_id)
        if (
            cohort is None
            or outcome is None
            or not _artifacts_match(trial, events[1], cohort, manifest, outcome)
        ):
            aggregate = replace(aggregate, failed=aggregate.failed + 1)
            continue
        payload = outcome.payload
        if payload.terminal_kind is TrialEventKind.COMPLETED:
            assert payload.treatment_confirmed_count is not None
            assert payload.control_confirmed_count is not None
            aggregate = replace(
                aggregate,
                completed=aggregate.completed + 1,
                treatment_count=aggregate.treatment_count + payload.treatment_count,
                control_count=aggregate.control_count + payload.control_count,
                treatment_confirmed=(
                    aggregate.treatment_confirmed + payload.treatment_confirmed_count
                ),
                control_confirmed=aggregate.control_confirmed + payload.control_confirmed_count,
            )
        elif payload.terminal_kind is TrialEventKind.CENSORED:
            aggregate = replace(aggregate, censored=aggregate.censored + 1)
        else:
            aggregate = replace(aggregate, failed=aggregate.failed + 1)
    return aggregate


def _artifacts_match(
    trial: MultiMarketExperimentTrialRegistration,
    terminal: StoredMultiMarketTrialEvent,
    cohort: UsNewsCatalystCohortArtifact,
    manifest: UsNewsCatalystSetupObservationManifest | None,
    outcome: UsNewsCatalystTrialOutcomeArtifact,
) -> bool:
    payload = outcome.payload
    expected_hashes = {cohort.artifact_id, outcome.artifact_id}
    if payload.observation_manifest_id is not None:
        if manifest is None:
            return False
        expected_hashes.add(manifest.manifest_id)
    elif manifest is not None:
        return False
    return (
        cohort.payload.trial_id == trial.trial_id
        and cohort.payload.strategy_version == trial.strategy_version
        and cohort.payload.session_date == trial.planned_start
        and payload.trial_id == trial.trial_id
        and payload.strategy_version == trial.strategy_version
        and payload.session_date == trial.planned_start
        and payload.cohort_artifact_id == cohort.artifact_id
        and _manifest_matches(manifest, payload.observation_manifest_id, cohort)
        and terminal.event.event_kind is payload.terminal_kind
        and terminal.event.occurred_at == payload.terminal_at
        and set(terminal.event.artifact_sha256s) == expected_hashes
        and terminal.event.reason_codes == payload.reason_codes
    )


def _manifest_matches(
    manifest: UsNewsCatalystSetupObservationManifest | None,
    expected_id: str | None,
    cohort: UsNewsCatalystCohortArtifact,
) -> bool:
    if expected_id is None:
        return manifest is None
    return (
        manifest is not None
        and manifest.manifest_id == expected_id
        and manifest.trial_id == cohort.payload.trial_id
        and manifest.cohort_artifact_id == cohort.artifact_id
    )


def _cohort_map(
    values: tuple[UsNewsCatalystCohortArtifact, ...],
) -> dict[str, UsNewsCatalystCohortArtifact]:
    result: dict[str, UsNewsCatalystCohortArtifact] = {}
    for value in values:
        _insert_unique(result, value.payload.trial_id, value)
    return result


def _manifest_map(
    values: tuple[UsNewsCatalystSetupObservationManifest, ...],
) -> dict[str, UsNewsCatalystSetupObservationManifest]:
    result: dict[str, UsNewsCatalystSetupObservationManifest] = {}
    for value in values:
        _insert_unique(result, value.trial_id, value)
    return result


def _outcome_map(
    values: tuple[UsNewsCatalystTrialOutcomeArtifact, ...],
) -> dict[str, UsNewsCatalystTrialOutcomeArtifact]:
    result: dict[str, UsNewsCatalystTrialOutcomeArtifact] = {}
    for value in values:
        _insert_unique(result, value.payload.trial_id, value)
    return result


def _insert_unique[ValueT](result: dict[str, ValueT], trial_id: str, value: ValueT) -> None:
    if trial_id in result:
        raise ValueError("duplicate US news-catalyst trial artifact")
    result[trial_id] = value


__all__ = (
    "UsNewsCatalystTrialAggregate",
    "aggregate_us_news_catalyst_trials",
)
