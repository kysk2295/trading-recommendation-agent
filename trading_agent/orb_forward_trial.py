from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.adaptive_evaluation_models import AdaptiveEvaluation
from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    EVALUATOR_VERSION,
    FEED_ENTITLEMENT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
    strategy_version_identity,
)
from trading_agent.daily_research_record_source import (
    InvalidDailyResearchRecordSourceError,
    load_daily_research_record_source,
)
from trading_agent.daily_research_sources import (
    OPTIONAL_ARTIFACTS,
    REQUIRED_ARTIFACTS,
    MissingResearchArtifactError,
    data_version,
    load_artifacts,
)
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    StrategyLifecycleState,
    StrategyVersionRegistration,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
    StoredExperimentTrialEvent,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.lane_contract_keys import (
    experiment_scope_key,
    lane_daily_snapshot_key,
    lane_manifest_key,
)
from trading_agent.lane_defaults import INTRADAY_MANIFEST, current_intraday_experiment_scope
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)
from trading_agent.lane_review_keys import lane_review_event_key
from trading_agent.lane_review_models import CURRENT_LANE_REVIEWER_VERSION
from trading_agent.lane_review_store import (
    InvalidLaneReviewSourceError,
    LaneReviewReader,
    UnsupportedLaneReviewSchemaError,
)
from trading_agent.strategy_factory import StrategyMode
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

ORB_DAILY_TRIAL_EVIDENCE_BUDGET: Final = (
    "adaptive_evaluation:1",
    "daily_research_record:1",
    "lane_daily_snapshot:1",
    "lane_review_event:1",
)

_ORB_CONTRACT: Final = strategy_contract(StrategyMode.ORB)
_ORB_SCOPE: Final = current_intraday_experiment_scope(_ORB_CONTRACT.hypothesis_id)
_ORB_SCOPE_KEY: Final = experiment_scope_key(_ORB_SCOPE)


class InvalidOrbForwardTrialSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "ORB 일일 forward trial의 exact immutable source를 확인하지 못했습니다"


class OrbTrialFailurePhase(StrEnum):
    PAPER_METRICS = "paper_metrics"
    DAILY_RESEARCH_RECORD = "daily_research_record"
    ADAPTIVE_EVALUATION = "adaptive_evaluation"
    LANE_FORWARD_VALIDATION = "lane_forward_validation"


class _PhaseAuditRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    started_at: dt.datetime
    exit_code: int
    status: Literal["ok", "failed"]

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        expected = "ok" if self.exit_code == 0 else "failed"
        if not _aware(self.started_at) or self.status != expected:
            raise ValueError("invalid phase audit row")
        return self


@dataclass(frozen=True, slots=True)
class OrbTrialRegistrationResult:
    created: bool
    registration: ExperimentTrialRegistration


@dataclass(frozen=True, slots=True)
class OrbTrialEventResult:
    created: bool
    event: ExperimentTrialEvent


def orb_shadow_trial_id(session_date: dt.date, strategy_version: str) -> str:
    version_digest = hashlib.sha256(strategy_version.encode()).hexdigest()[:12]
    return f"orb-shadow-{session_date:%Y%m%d}-{version_digest}"


def orb_shadow_trial_data_version() -> str:
    material = json.dumps(
        {
            "data_contract": CURRENT_DATA_CONTRACT,
            "optional_artifacts": OPTIONAL_ARTIFACTS,
            "required_artifacts": REQUIRED_ARTIFACTS,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def register_orb_shadow_trial(
    *,
    lane_registry: LaneRegistryReader,
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
    runtime_code_version: str,
    registered_at: dt.datetime,
) -> OrbTrialRegistrationResult:
    try:
        if not _aware(registered_at):
            raise InvalidOrbForwardTrialSourceError
        bounds = regular_session_bounds(session_date)
        if bounds is None:
            raise InvalidOrbForwardTrialSourceError
        version = _verified_orb_sources(
            lane_registry,
            experiment_ledger,
            session_date,
            runtime_code_version,
        )
        trial_id = orb_shadow_trial_id(session_date, version.strategy_version)
        trials = experiment_ledger.trials()
        same_session = tuple(
            stored
            for stored in trials
            if stored.registration.planned_start == session_date
            and stored.registration.planned_end == session_date
            and stored.registration.experiment_scope == _ORB_SCOPE
            and stored.registration.experiment_scope_key == _ORB_SCOPE_KEY
        )
        if same_session:
            if (
                len(same_session) != 1
                or same_session[0].registration.strategy_version != version.strategy_version
                or same_session[0].registration.trial_id != trial_id
            ):
                raise InvalidOrbForwardTrialSourceError
            existing = same_session[0].registration
            if registered_at < existing.registered_at:
                raise InvalidOrbForwardTrialSourceError
            expected = _trial_registration(
                version,
                session_date=session_date,
                registered_at=existing.registered_at,
            )
            if existing != expected:
                raise InvalidOrbForwardTrialSourceError
            return OrbTrialRegistrationResult(False, existing)
        if registered_at >= bounds[0]:
            raise InvalidOrbForwardTrialSourceError
        registration = _trial_registration(
            version,
            session_date=session_date,
            registered_at=registered_at,
        )
    except InvalidOrbForwardTrialSourceError:
        raise
    except (
        InvalidExperimentLedgerSourceError,
        UnsupportedExperimentLedgerSchemaError,
        InvalidLaneRegistrySourceError,
        UnsupportedLaneRegistrySchemaError,
        ValidationError,
        sqlite3.Error,
        OSError,
        ValueError,
    ):
        raise InvalidOrbForwardTrialSourceError from None

    with experiment_ledger.writer() as writer:
        created = writer.register_trial(registration)
    return OrbTrialRegistrationResult(created, registration)


def start_orb_shadow_trial(
    *,
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
    started_at: dt.datetime,
) -> OrbTrialEventResult:
    try:
        bounds = _event_bounds(session_date, started_at)
        _, registration, events = _verified_daily_trial(
            experiment_ledger,
            session_date,
        )
        if events:
            if len(events) != 1:
                raise InvalidOrbForwardTrialSourceError
            first = events[0]
            if (
                first.event.event_kind is not TrialEventKind.STARTED
                or first.event.sequence != 1
                or first.event.previous_event_key is not None
                or not bounds[0] <= first.event.occurred_at < bounds[1]
            ):
                raise InvalidOrbForwardTrialSourceError
            return OrbTrialEventResult(False, first.event)
        if not bounds[0] <= started_at < bounds[1]:
            raise InvalidOrbForwardTrialSourceError
        event = ExperimentTrialEvent(
            trial_id=registration.trial_id,
            sequence=1,
            event_kind=TrialEventKind.STARTED,
            occurred_at=started_at,
            artifact_sha256s=(),
            reason_codes=(),
            previous_event_key=None,
        )
    except InvalidOrbForwardTrialSourceError:
        raise
    except _SOURCE_ERRORS:
        raise InvalidOrbForwardTrialSourceError from None

    with experiment_ledger.writer() as writer:
        created = writer.append_trial_event(event)
    return OrbTrialEventResult(created, event)


def finalize_orb_shadow_trial(
    *,
    experiment_ledger: ExperimentLedgerStore,
    lane_registry: LaneRegistryReader,
    review_ledger: LaneReviewReader,
    session: Path,
    session_date: dt.date,
    occurred_at: dt.datetime,
) -> OrbTrialEventResult:
    try:
        bounds = _event_bounds(session_date, occurred_at)
        if occurred_at < bounds[1]:
            raise InvalidOrbForwardTrialSourceError
        version, registration, events = _verified_daily_trial(
            experiment_ledger,
            session_date,
        )
        if not events or len(events) > 2:
            raise InvalidOrbForwardTrialSourceError
        started = next(iter(events))
        existing_terminal = next(iter(events[1:]), None)
        if (
            started.event.event_kind is not TrialEventKind.STARTED
            or not bounds[0] <= started.event.occurred_at < bounds[1]
        ):
            raise InvalidOrbForwardTrialSourceError
        event_kind, artifacts, reasons = _verified_terminal_evidence(
            lane_registry,
            review_ledger,
            session,
            session_date,
            occurred_at,
            version,
            registration,
        )
        event_occurred_at = occurred_at if existing_terminal is None else existing_terminal.event.occurred_at
        event = ExperimentTrialEvent(
            trial_id=registration.trial_id,
            sequence=2,
            event_kind=event_kind,
            occurred_at=event_occurred_at,
            artifact_sha256s=artifacts,
            reason_codes=reasons,
            previous_event_key=started.event_key,
        )
        if existing_terminal is not None:
            if existing_terminal.event != event:
                raise InvalidOrbForwardTrialSourceError
            return OrbTrialEventResult(False, existing_terminal.event)
    except InvalidOrbForwardTrialSourceError:
        raise
    except _SOURCE_ERRORS:
        raise InvalidOrbForwardTrialSourceError from None

    with experiment_ledger.writer() as writer:
        created = writer.append_trial_event(event)
    return OrbTrialEventResult(created, event)


def fail_orb_shadow_trial(
    *,
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
    phase: OrbTrialFailurePhase,
    audit: Path,
    occurred_at: dt.datetime,
) -> OrbTrialEventResult:
    try:
        bounds = _event_bounds(session_date, occurred_at)
        if occurred_at < bounds[1] or not isinstance(phase, OrbTrialFailurePhase):
            raise InvalidOrbForwardTrialSourceError
        _, registration, events = _verified_daily_trial(
            experiment_ledger,
            session_date,
        )
        if not events or len(events) > 2:
            raise InvalidOrbForwardTrialSourceError
        started = next(iter(events))
        existing_terminal = next(iter(events[1:]), None)
        if (
            started.event.event_kind is not TrialEventKind.STARTED
            or not bounds[0] <= started.event.occurred_at < bounds[1]
        ):
            raise InvalidOrbForwardTrialSourceError
        audit_sha256 = _verified_failed_audit(
            audit,
            session_date=session_date,
            occurred_at=occurred_at,
            close_at=bounds[1],
        )
        event_occurred_at = occurred_at if existing_terminal is None else existing_terminal.event.occurred_at
        event = ExperimentTrialEvent(
            trial_id=registration.trial_id,
            sequence=2,
            event_kind=TrialEventKind.FAILED,
            occurred_at=event_occurred_at,
            artifact_sha256s=(audit_sha256,),
            reason_codes=(f"{phase.value}_phase_failed",),
            previous_event_key=started.event_key,
        )
        if existing_terminal is not None:
            if existing_terminal.event != event:
                raise InvalidOrbForwardTrialSourceError
            return OrbTrialEventResult(False, existing_terminal.event)
    except InvalidOrbForwardTrialSourceError:
        raise
    except _SOURCE_ERRORS:
        raise InvalidOrbForwardTrialSourceError from None

    with experiment_ledger.writer() as writer:
        created = writer.append_trial_event(event)
    return OrbTrialEventResult(created, event)


def _verified_orb_sources(
    lane_registry: LaneRegistryReader,
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
    runtime_code_version: str,
) -> StrategyVersionRegistration:
    _require_exact_lane_contracts(lane_registry)
    return _verified_orb_experiment_source(
        experiment_ledger,
        session_date,
        runtime_code_version=runtime_code_version,
        expected_strategy_version=None,
    )


def _require_exact_lane_contracts(lane_registry: LaneRegistryReader) -> None:
    if not lane_registry.is_initialized():
        raise InvalidOrbForwardTrialSourceError
    manifests = tuple(
        stored
        for stored in lane_registry.manifests()
        if stored.manifest.lane_id is LaneId.INTRADAY_MOMENTUM
        and stored.manifest.manifest_version == INTRADAY_MANIFEST.manifest_version
    )
    scopes = tuple(
        stored for stored in lane_registry.experiment_scopes() if stored.scope.hypothesis_id == _ORB_SCOPE.hypothesis_id
    )
    if (
        len(manifests) != 1
        or manifests[0].manifest != INTRADAY_MANIFEST
        or manifests[0].manifest_key != lane_manifest_key(INTRADAY_MANIFEST)
        or len(scopes) != 1
        or scopes[0].scope != _ORB_SCOPE
        or scopes[0].scope_key != _ORB_SCOPE_KEY
    ):
        raise InvalidOrbForwardTrialSourceError


def _verified_orb_experiment_source(
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
    *,
    runtime_code_version: str | None,
    expected_strategy_version: str | None,
) -> StrategyVersionRegistration:
    if not experiment_ledger.is_initialized():
        raise InvalidOrbForwardTrialSourceError
    hypotheses = tuple(
        stored
        for stored in experiment_ledger.hypotheses()
        if stored.registration.hypothesis_id == _ORB_CONTRACT.hypothesis_id
    )
    if runtime_code_version is not None:
        expected_strategy_version = strategy_version_identity(
            StrategyMode.ORB,
            runtime_code_version,
        )
    if expected_strategy_version is None:
        raise InvalidOrbForwardTrialSourceError
    versions = tuple(
        stored
        for stored in experiment_ledger.strategy_versions()
        if stored.registration.strategy_version == expected_strategy_version
    )
    if len(hypotheses) != 1 or len(versions) != 1:
        raise InvalidOrbForwardTrialSourceError
    hypothesis = hypotheses[0].registration
    version = versions[0].registration
    if (
        hypothesis.experiment_scope != _ORB_SCOPE
        or hypothesis.experiment_scope_key != _ORB_SCOPE_KEY
        or hypothesis.primary_lane is not LaneId.INTRADAY_MOMENTUM
        or hypothesis.hypothesis != _ORB_CONTRACT.hypothesis
        or hypothesis.falsification_rule != _ORB_CONTRACT.falsification_rule
        or version.strategy_id != StrategyMode.ORB.value
        or version.strategy_version != expected_strategy_version
        or (
            version.strategy_version != _ORB_CONTRACT.strategy_version
            and version.strategy_version
            != strategy_version_identity(StrategyMode.ORB, version.code_version)
        )
        or version.hypothesis_id != _ORB_CONTRACT.hypothesis_id
        or version.experiment_scope_key != _ORB_SCOPE_KEY
        or version.lane_id is not LaneId.INTRADAY_MOMENTUM
        or (runtime_code_version is not None and version.code_version != runtime_code_version)
        or version.parameter_set != _ORB_CONTRACT.parameter_set
        or version.data_contract != CURRENT_DATA_CONTRACT
        or version.cost_model != CURRENT_COST_MODEL
        or version.portfolio_policy != SHADOW_PORTFOLIO_POLICY
        or version.source_registered_at != _ORB_SCOPE.registered_at
    ):
        raise InvalidOrbForwardTrialSourceError
    state = experiment_ledger.lifecycle_state(version.strategy_version, session_date)
    if state is None or state.event.to_state is StrategyLifecycleState.REJECTED:
        raise InvalidOrbForwardTrialSourceError
    return version


def _verified_daily_trial(
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
) -> tuple[
    StrategyVersionRegistration,
    ExperimentTrialRegistration,
    tuple[StoredExperimentTrialEvent, ...],
]:
    matches = tuple(
        stored.registration
        for stored in experiment_ledger.trials()
        if stored.registration.planned_start == session_date
        and stored.registration.planned_end == session_date
        and stored.registration.experiment_scope == _ORB_SCOPE
        and stored.registration.experiment_scope_key == _ORB_SCOPE_KEY
    )
    if len(matches) != 1:
        raise InvalidOrbForwardTrialSourceError
    registration = matches[0]
    version = _verified_orb_experiment_source(
        experiment_ledger,
        session_date,
        runtime_code_version=None,
        expected_strategy_version=registration.strategy_version,
    )
    expected_id = orb_shadow_trial_id(session_date, version.strategy_version)
    if registration.trial_id != expected_id:
        raise InvalidOrbForwardTrialSourceError
    expected = _trial_registration(
        version,
        session_date=session_date,
        registered_at=registration.registered_at,
    )
    bounds = regular_session_bounds(session_date)
    if bounds is None or registration != expected or registration.registered_at >= bounds[0]:
        raise InvalidOrbForwardTrialSourceError
    return version, registration, experiment_ledger.trial_events(registration.trial_id)


def _verified_terminal_evidence(
    lane_registry: LaneRegistryReader,
    review_ledger: LaneReviewReader,
    session: Path,
    session_date: dt.date,
    occurred_at: dt.datetime,
    version: StrategyVersionRegistration,
    registration: ExperimentTrialRegistration,
) -> tuple[TrialEventKind, tuple[str, ...], tuple[str, ...]]:
    _require_exact_lane_contracts(lane_registry)
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        raise InvalidOrbForwardTrialSourceError
    stored_snapshot = lane_registry.daily_snapshot(LaneId.INTRADAY_MOMENTUM, session_date)
    if stored_snapshot is None:
        raise InvalidOrbForwardTrialSourceError
    snapshot = stored_snapshot.snapshot
    snapshot_key = str(lane_daily_snapshot_key(snapshot))
    if (
        str(stored_snapshot.snapshot_key) != snapshot_key
        or snapshot.lane_id is not LaneId.INTRADAY_MOMENTUM
        or snapshot.session_date != session_date
        or snapshot.manifest_key != lane_manifest_key(INTRADAY_MANIFEST)
        or snapshot.experiment_scope_keys != (_ORB_SCOPE_KEY,)
        or snapshot.finalized_at < bounds[1]
        or snapshot.finalized_at > occurred_at
        or snapshot.open_order_count != 0
        or snapshot.open_position_count != 0
        or snapshot.planned_open_risk != 0
        or snapshot.unrealized_pnl != 0
    ):
        raise InvalidOrbForwardTrialSourceError

    if not review_ledger.is_initialized():
        raise InvalidOrbForwardTrialSourceError
    stored_review = review_ledger.review_event(
        snapshot_key,
        _ORB_SCOPE_KEY,
        CURRENT_LANE_REVIEWER_VERSION,
    )
    if stored_review is None:
        raise InvalidOrbForwardTrialSourceError
    review = stored_review.event
    review_key = str(lane_review_event_key(review))
    if (
        str(stored_review.event_key) != review_key
        or review.lane_id is not LaneId.INTRADAY_MOMENTUM
        or review.session_date != session_date
        or review.snapshot_key != snapshot_key
        or review.experiment_scope_key != _ORB_SCOPE_KEY
        or review.strategy_version != version.strategy_version
        or review.evaluator_version != registration.evaluator_version
        or review.reviewer_version != CURRENT_LANE_REVIEWER_VERSION
        or review.automatic_state_change_allowed
        or review.order_authority_change_allowed
        or snapshot.finalized_at > review.reviewed_at
        or review.reviewed_at > occurred_at
    ):
        raise InvalidOrbForwardTrialSourceError

    source = load_daily_research_record_source(
        session,
        session_date,
        StrategyMode.ORB,
        _ORB_SCOPE_KEY,
    )
    record = source.record
    current_artifacts = load_artifacts(session)
    if (
        record.session_date != session_date
        or record.strategy != StrategyMode.ORB.value
        or record.strategy_version != version.strategy_version
        or record.experiment_scope != _ORB_SCOPE
        or record.experiment_scope_key != _ORB_SCOPE_KEY
        or record.code_version != version.code_version
        or record.evaluator_version != registration.evaluator_version
        or record.feed_entitlement != registration.feed_entitlement
        or registration.data_version != orb_shadow_trial_data_version()
        or record.parameter_set != version.parameter_set
        or record.cost_model != version.cost_model
        or record.portfolio_policy != version.portfolio_policy
        or not _aware(record.recorded_at)
        or record.recorded_at > snapshot.finalized_at
        or source.raw_sha256 != review.daily_record_sha256
        or record.record_id != review.daily_record_id
        or record.artifact_checksums != current_artifacts
        or record.data_version != data_version(current_artifacts)
    ):
        raise InvalidOrbForwardTrialSourceError

    adaptive_raw = (session / "adaptive_evaluation" / "adaptive_evaluation.json").read_bytes()
    adaptive_sha256 = hashlib.sha256(adaptive_raw).hexdigest()
    adaptive = AdaptiveEvaluation.model_validate_json(adaptive_raw)
    if (
        adaptive.as_of != session_date
        or adaptive.strategy_version != version.strategy_version
        or adaptive.evaluator_version != registration.evaluator_version
        or adaptive_sha256 != review.adaptive_evaluation_sha256
    ):
        raise InvalidOrbForwardTrialSourceError

    artifacts = tuple(
        sorted(
            {
                source.raw_sha256,
                adaptive_sha256,
                snapshot_key,
                review_key,
            }
        )
    )
    if len(artifacts) != 4:
        raise InvalidOrbForwardTrialSourceError
    reasons: list[str] = []
    if not record.session_quality.forward_day_eligible:
        reasons.append("forward_day_ineligible")
    if record.incidents:
        reasons.append("daily_incidents_present")
    if not snapshot.data_quality_complete:
        reasons.append("snapshot_data_quality_incomplete")
    if snapshot.incidents:
        reasons.append("snapshot_incidents_present")
    canonical_reasons = tuple(sorted(reasons))
    event_kind = TrialEventKind.COMPLETED if not canonical_reasons else TrialEventKind.CENSORED
    return event_kind, artifacts, canonical_reasons


def _verified_failed_audit(
    audit: Path,
    *,
    session_date: dt.date,
    occurred_at: dt.datetime,
    close_at: dt.datetime,
) -> str:
    raw = audit.read_bytes()
    reader = csv.DictReader(raw.decode("utf-8").splitlines())
    if tuple(reader.fieldnames or ()) != ("started_at", "exit_code", "status"):
        raise InvalidOrbForwardTrialSourceError
    rows = tuple(_PhaseAuditRow.model_validate(row) for row in reader)
    if not rows:
        raise InvalidOrbForwardTrialSourceError
    latest = rows[-1]
    if (
        latest.started_at.astimezone(NEW_YORK).date() != session_date
        or latest.started_at < close_at
        or latest.started_at > occurred_at
        or latest.exit_code == 0
        or latest.status != "failed"
    ):
        raise InvalidOrbForwardTrialSourceError
    return hashlib.sha256(raw).hexdigest()


def _trial_registration(
    version: StrategyVersionRegistration,
    *,
    session_date: dt.date,
    registered_at: dt.datetime,
) -> ExperimentTrialRegistration:
    return ExperimentTrialRegistration(
        trial_id=orb_shadow_trial_id(session_date, version.strategy_version),
        strategy_version=version.strategy_version,
        trial_kind=TrialKind.SHADOW_FORWARD,
        experiment_scope=_ORB_SCOPE,
        experiment_scope_key=_ORB_SCOPE_KEY,
        evaluator_version=EVALUATOR_VERSION,
        data_version=orb_shadow_trial_data_version(),
        feed_entitlement=FEED_ENTITLEMENT,
        planned_start=session_date,
        planned_end=session_date,
        registered_at=registered_at,
        evidence_budget=ORB_DAILY_TRIAL_EVIDENCE_BUDGET,
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _event_bounds(
    session_date: dt.date,
    occurred_at: dt.datetime,
) -> tuple[dt.datetime, dt.datetime]:
    bounds = regular_session_bounds(session_date)
    if bounds is None or not _aware(occurred_at) or occurred_at.astimezone(NEW_YORK).date() != session_date:
        raise InvalidOrbForwardTrialSourceError
    return bounds


_SOURCE_ERRORS = (
    csv.Error,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
    InvalidLaneRegistrySourceError,
    UnsupportedLaneRegistrySchemaError,
    InvalidLaneReviewSourceError,
    UnsupportedLaneReviewSchemaError,
    InvalidDailyResearchRecordSourceError,
    MissingResearchArtifactError,
    ValidationError,
    sqlite3.Error,
    OSError,
    UnicodeError,
    ValueError,
)
