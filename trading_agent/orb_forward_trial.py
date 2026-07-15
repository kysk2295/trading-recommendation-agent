from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    EVALUATOR_VERSION,
    FEED_ENTITLEMENT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
)
from trading_agent.daily_research_sources import OPTIONAL_ARTIFACTS, REQUIRED_ARTIFACTS
from trading_agent.experiment_ledger_models import (
    ExperimentTrialRegistration,
    StrategyLifecycleState,
    StrategyVersionRegistration,
    TrialKind,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.lane_contract_keys import experiment_scope_key, lane_manifest_key
from trading_agent.lane_defaults import INTRADAY_MANIFEST, current_intraday_experiment_scope
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)
from trading_agent.strategy_factory import StrategyMode
from trading_agent.us_equity_calendar import regular_session_bounds

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


@dataclass(frozen=True, slots=True)
class OrbTrialRegistrationResult:
    created: bool
    registration: ExperimentTrialRegistration


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
            if stored.registration.strategy_version == version.strategy_version
            and stored.registration.planned_start == session_date
            and stored.registration.planned_end == session_date
        )
        if same_session:
            if len(same_session) != 1 or same_session[0].registration.trial_id != trial_id:
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


def _verified_orb_sources(
    lane_registry: LaneRegistryReader,
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
    runtime_code_version: str,
) -> StrategyVersionRegistration:
    if not lane_registry.is_initialized() or not experiment_ledger.is_initialized():
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

    hypotheses = tuple(
        stored
        for stored in experiment_ledger.hypotheses()
        if stored.registration.hypothesis_id == _ORB_CONTRACT.hypothesis_id
    )
    versions = tuple(
        stored
        for stored in experiment_ledger.strategy_versions()
        if stored.registration.strategy_version == _ORB_CONTRACT.strategy_version
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
        or version.hypothesis_id != _ORB_CONTRACT.hypothesis_id
        or version.experiment_scope_key != _ORB_SCOPE_KEY
        or version.lane_id is not LaneId.INTRADAY_MOMENTUM
        or version.code_version != runtime_code_version
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
