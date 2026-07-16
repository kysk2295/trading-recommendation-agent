from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import override

from pydantic import ValidationError

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
    StrategyResearchContract,
    strategy_contract,
    strategy_version_identity,
)
from trading_agent.experiment_ledger_keys import (
    hypothesis_registration_key,
    strategy_version_registration_key,
)
from trading_agent.experiment_ledger_models import (
    HypothesisRegistration,
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    StrategyVersionRegistration,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.lane_contract_keys import (
    experiment_scope_key,
    lane_manifest_key,
)
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    INTRADAY_MANIFEST,
)
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)
from trading_agent.strategy_factory import StrategyMode
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class InvalidExperimentLedgerBootstrapSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "전역 experiment ledger bootstrap의 immutable lane source 또는 입력 계약이 유효하지 않습니다"


@dataclass(frozen=True, slots=True)
class ExperimentLedgerBootstrapResult:
    hypotheses_created: int
    versions_created: int
    lifecycle_events_created: int
    effective_session_date: dt.date


@dataclass(frozen=True, slots=True)
class _BootstrapRegistration:
    hypothesis: HypothesisRegistration
    version: StrategyVersionRegistration
    lifecycle_event: StrategyLifecycleEvent


@dataclass(frozen=True, slots=True)
class _BootstrapTimeline:
    hypothesis_recorded_at: dt.datetime
    version_recorded_at: dt.datetime
    effective_session_date: dt.date
    code_version_rollover: bool


def bootstrap_current_intraday_experiments(
    *,
    lane_registry: LaneRegistryReader,
    experiment_ledger: ExperimentLedgerStore,
    code_version: str,
    recorded_at: dt.datetime,
) -> ExperimentLedgerBootstrapResult:
    _require_aware(recorded_at)
    contracts = _verified_current_contracts(lane_registry)
    _verify_bootstrap_input(contracts, code_version, recorded_at)
    # Reader validation requires the current schema; the empty lease only applies v1->v2 DDL.
    with experiment_ledger.writer():
        pass
    timeline = _bootstrap_timeline(
        experiment_ledger,
        contracts,
        code_version,
        recorded_at,
    )
    registrations = _build_registrations(
        contracts,
        code_version=code_version,
        timeline=timeline,
    )

    with experiment_ledger.writer() as writer:
        hypotheses_created = sum(writer.register_hypothesis(registration.hypothesis) for registration in registrations)
        versions_created = sum(writer.register_strategy_version(registration.version) for registration in registrations)
        lifecycle_events_created = sum(
            writer.append_lifecycle_event(registration.lifecycle_event) for registration in registrations
        )
    return ExperimentLedgerBootstrapResult(
        hypotheses_created=hypotheses_created,
        versions_created=versions_created,
        lifecycle_events_created=lifecycle_events_created,
        effective_session_date=timeline.effective_session_date,
    )


def _verified_current_contracts(
    lane_registry: LaneRegistryReader,
) -> tuple[tuple[StrategyMode, StrategyResearchContract], ...]:
    try:
        if not lane_registry.is_initialized():
            raise InvalidExperimentLedgerBootstrapSourceError
        manifests = lane_registry.manifests()
        scopes = lane_registry.experiment_scopes()
    except InvalidExperimentLedgerBootstrapSourceError:
        raise
    except (
        InvalidLaneRegistrySourceError,
        UnsupportedLaneRegistrySchemaError,
        ValidationError,
        sqlite3.Error,
        OSError,
        ValueError,
    ) as error:
        raise InvalidExperimentLedgerBootstrapSourceError from error

    current_manifests = tuple(
        stored
        for stored in manifests
        if stored.manifest.lane_id is INTRADAY_MANIFEST.lane_id
        and stored.manifest.manifest_version == INTRADAY_MANIFEST.manifest_version
    )
    if (
        len(current_manifests) != 1
        or current_manifests[0].manifest != INTRADAY_MANIFEST
        or current_manifests[0].manifest_key != lane_manifest_key(INTRADAY_MANIFEST)
    ):
        raise InvalidExperimentLedgerBootstrapSourceError

    for expected_scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES:
        stored_matches = tuple(
            stored for stored in scopes if stored.scope.hypothesis_id == expected_scope.hypothesis_id
        )
        if (
            len(stored_matches) != 1
            or stored_matches[0].scope != expected_scope
            or stored_matches[0].scope_key != experiment_scope_key(expected_scope)
        ):
            raise InvalidExperimentLedgerBootstrapSourceError

    contracts = tuple((mode, strategy_contract(mode)) for mode in StrategyMode)
    expected_scope_by_hypothesis = {scope.hypothesis_id: scope for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES}
    if any(
        contract.experiment_scope != expected_scope_by_hypothesis.get(contract.hypothesis_id)
        for _, contract in contracts
    ):
        raise InvalidExperimentLedgerBootstrapSourceError
    return contracts


def _verify_bootstrap_input(
    contracts: tuple[tuple[StrategyMode, StrategyResearchContract], ...],
    code_version: str,
    recorded_at: dt.datetime,
) -> None:
    _ = _build_registrations(
        contracts,
        code_version=code_version,
        timeline=_BootstrapTimeline(
            hypothesis_recorded_at=recorded_at,
            version_recorded_at=recorded_at,
            effective_session_date=_next_regular_session(recorded_at),
            code_version_rollover=False,
        ),
    )


def _bootstrap_timeline(
    experiment_ledger: ExperimentLedgerStore,
    contracts: tuple[tuple[StrategyMode, StrategyResearchContract], ...],
    code_version: str,
    requested_at: dt.datetime,
) -> _BootstrapTimeline:
    hypothesis_ids = {contract.hypothesis_id for _, contract in contracts}
    expected_versions = {
        strategy_version_identity(mode, code_version)
        for mode, _ in contracts
    }
    hypothesis_times = {
        stored.registration.ledger_recorded_at
        for stored in experiment_ledger.hypotheses()
        if stored.registration.hypothesis_id in hypothesis_ids
    }
    if len(hypothesis_times) > 1:
        raise InvalidExperimentLedgerBootstrapSourceError
    if hypothesis_times:
        hypothesis_recorded_at = next(iter(hypothesis_times))
        if requested_at < hypothesis_recorded_at:
            raise InvalidExperimentLedgerBootstrapSourceError
    else:
        hypothesis_recorded_at = requested_at

    versions = tuple(
        stored.registration.ledger_recorded_at
        for stored in experiment_ledger.strategy_versions()
        if stored.registration.strategy_version in expected_versions
    )
    if len(versions) not in (0, len(expected_versions)):
        raise InvalidExperimentLedgerBootstrapSourceError
    if not versions:
        return _BootstrapTimeline(
            hypothesis_recorded_at=hypothesis_recorded_at,
            version_recorded_at=requested_at,
            effective_session_date=_next_regular_session(requested_at),
            code_version_rollover=bool(hypothesis_times),
        )

    version_times = set(versions)
    if len(version_times) != 1:
        raise InvalidExperimentLedgerBootstrapSourceError
    version_recorded_at = next(iter(version_times))
    if requested_at < version_recorded_at:
        raise InvalidExperimentLedgerBootstrapSourceError
    lifecycle_events = tuple(
        experiment_ledger.lifecycle_events(strategy_version)
        for strategy_version in expected_versions
    )
    if not all(len(events) == 1 for events in lifecycle_events):
        raise InvalidExperimentLedgerBootstrapSourceError
    events = tuple(events[0].event for events in lifecycle_events)
    effective_dates = {event.effective_session_date for event in events}
    reason_sets = {event.reason_codes for event in events}
    supported_reason_sets = {
        ("existing_contract_import",),
        ("code_version_rollover", "existing_contract_import"),
    }
    if (
        len(effective_dates) != 1
        or len(reason_sets) != 1
        or not reason_sets <= supported_reason_sets
        or any(
            event.event_kind is not StrategyLifecycleEventKind.REGISTRATION
            or event.decided_at != version_recorded_at
            for event in events
        )
    ):
        raise InvalidExperimentLedgerBootstrapSourceError
    return _BootstrapTimeline(
        hypothesis_recorded_at=hypothesis_recorded_at,
        version_recorded_at=version_recorded_at,
        effective_session_date=next(iter(effective_dates)),
        code_version_rollover=("code_version_rollover", "existing_contract_import") in reason_sets,
    )


def _build_registrations(
    contracts: tuple[tuple[StrategyMode, StrategyResearchContract], ...],
    *,
    code_version: str,
    timeline: _BootstrapTimeline,
) -> tuple[_BootstrapRegistration, ...]:
    registrations: list[_BootstrapRegistration] = []
    try:
        for mode, contract in contracts:
            scope = contract.experiment_scope
            hypothesis = HypothesisRegistration(
                hypothesis_id=contract.hypothesis_id,
                experiment_scope=scope,
                experiment_scope_key=experiment_scope_key(scope),
                primary_lane=scope.primary_lane,
                hypothesis=contract.hypothesis,
                falsification_rule=contract.falsification_rule,
                source_registered_at=scope.registered_at,
                ledger_recorded_at=timeline.hypothesis_recorded_at,
            )
            version = StrategyVersionRegistration(
                strategy_id=mode.value,
                strategy_version=strategy_version_identity(mode, code_version),
                hypothesis_id=contract.hypothesis_id,
                experiment_scope_key=experiment_scope_key(scope),
                lane_id=scope.primary_lane,
                code_version=code_version,
                parameter_set=contract.parameter_set,
                data_contract=CURRENT_DATA_CONTRACT,
                cost_model=CURRENT_COST_MODEL,
                portfolio_policy=SHADOW_PORTFOLIO_POLICY,
                source_registered_at=scope.registered_at,
                ledger_recorded_at=timeline.version_recorded_at,
            )
            evidence_keys = tuple(
                sorted(
                    (
                        str(experiment_scope_key(scope)),
                        str(hypothesis_registration_key(hypothesis)),
                        str(strategy_version_registration_key(version)),
                    )
                )
            )
            lifecycle_event = StrategyLifecycleEvent(
                strategy_version=version.strategy_version,
                sequence=1,
                event_kind=StrategyLifecycleEventKind.REGISTRATION,
                from_state=None,
                to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
                policy_version="strategy_lifecycle_v1",
                decision_session_date=timeline.version_recorded_at.astimezone(NEW_YORK).date(),
                effective_session_date=timeline.effective_session_date,
                decided_at=timeline.version_recorded_at,
                evidence_keys=evidence_keys,
                reason_codes=(
                    ("code_version_rollover", "existing_contract_import")
                    if timeline.code_version_rollover
                    else ("existing_contract_import",)
                ),
                previous_event_key=None,
            )
            registrations.append(_BootstrapRegistration(hypothesis, version, lifecycle_event))
    except (ValidationError, ValueError) as error:
        raise InvalidExperimentLedgerBootstrapSourceError from error
    return tuple(registrations)


def _next_regular_session(recorded_at: dt.datetime) -> dt.date:
    decision_date = recorded_at.astimezone(NEW_YORK).date()
    for offset in range(1, 11):
        candidate = decision_date + dt.timedelta(days=offset)
        if regular_session_bounds(candidate) is not None:
            return candidate
    raise InvalidExperimentLedgerBootstrapSourceError


def _require_aware(recorded_at: dt.datetime) -> None:
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise InvalidExperimentLedgerBootstrapSourceError
