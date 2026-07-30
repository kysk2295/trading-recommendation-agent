from __future__ import annotations

import datetime as dt
import hashlib
import json

from pydantic import ValidationError

from trading_agent.daily_research_contract import strategy_version_identity
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.forward_runtime_readiness import evaluate_forward_runtime_readiness
from trading_agent.future_session_plan_models import (
    DeferredTrialRegistrationState,
    FutureSessionArtifactLayout,
    FutureSessionPlanDecision,
    FutureSessionPlanRequest,
    JobTimingSpec,
    ReadyToPrepareSessionPlan,
    RuntimeEnvironmentAttestation,
    SessionCalendarProvenance,
    StrategyRegistrationIdentity,
    WaitingAuthorityReason,
    WaitingSessionAuthority,
    canonical_request_json,
    plan_content_sha256,
)
from trading_agent.future_session_us_payloads import attest_us_runtime, build_us_jobs
from trading_agent.strategy_factory import StrategyMode
from trading_agent.us_equity_calendar import (
    EARLY_CLOSE_DAYS,
    FULL_DAY_HOLIDAYS,
    PUBLISHED_CALENDAR_YEARS,
    UnsupportedUsEquityCalendarDateError,
    next_regular_session,
    regular_session_bounds,
)


def compile_us_future_session_plan(
    request: FutureSessionPlanRequest,
) -> FutureSessionPlanDecision:
    try:
        target = next_regular_session(request.after_date)
    except UnsupportedUsEquityCalendarDateError:
        return _waiting(
            request,
            None,
            WaitingAuthorityReason.CALENDAR_AUTHORITY_MISSING,
        )
    lane_registry = request.lane_registry
    execution_database = request.execution_database
    if lane_registry is None or execution_database is None:
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.RUNTIME_AUTHORITY_MISSING,
        )
    runtime_environment, environment_reason = attest_us_runtime(request)
    if runtime_environment is None:
        reason = (
            WaitingAuthorityReason.FROZEN_RUNTIME_STORE_SCHEMA_INCOMPATIBLE
            if environment_reason == "frozen_runtime_store_schema_incompatible"
            else WaitingAuthorityReason.RUNTIME_ENVIRONMENT_INVALID
        )
        return _waiting(request, target, reason)
    readiness = evaluate_forward_runtime_readiness(
        runtime_dir=request.frozen_runtime.directory,
        expected_head=request.frozen_runtime.commit_sha,
        required_commits=request.required_runtime_commits,
        session_date=target,
        experiment_ledger=request.experiment_ledger,
        lane_registry=lane_registry,
        execution_database=execution_database,
        cycles=request.cycles,
        interval_seconds=request.interval_seconds,
        kis_server_attempts=request.kis_server_attempts,
        eod_last_bar_semantic_attempts=request.eod_last_bar_semantic_attempts,
    )
    if not readiness.ready:
        runtime_invalid = {
            "runtime_not_frozen",
            "required_commit_missing",
            "runtime_config_mismatch",
        }.intersection(readiness.blockers)
        reason = (
            WaitingAuthorityReason.FROZEN_RUNTIME_INVALID
            if runtime_invalid
            else WaitingAuthorityReason.RUNTIME_AUTHORITY_MISSING
        )
        return _waiting(request, target, reason)
    registrations = _registrations(request, target)
    if registrations is None:
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.RUNTIME_AUTHORITY_AMBIGUOUS,
        )
    bounds = regular_session_bounds(target)
    if bounds is None:
        return _waiting(
            request,
            target,
            WaitingAuthorityReason.CALENDAR_AUTHORITY_INVALID,
        )
    calendar = SessionCalendarProvenance(
        source="XNYS",
        source_version="tracked-xnys-2023-2028-v1",
        evidence_sha256=_calendar_sha256(),
    )
    orb = next(
        item.strategy_version
        for item in registrations
        if item.strategy_version.startswith("orb_")
    )
    jobs = build_us_jobs(request, target, orb)
    return _ready(
        request,
        target,
        calendar,
        registrations,
        jobs,
        runtime_environment,
    )


def _registrations(
    request: FutureSessionPlanRequest,
    target: dt.date,
) -> tuple[StrategyRegistrationIdentity, ...] | None:
    reader = ExperimentLedgerReader(request.experiment_ledger)
    expected = {
        strategy_version_identity(mode, request.frozen_runtime.commit_sha)
        for mode in StrategyMode
    }
    try:
        versions = tuple(
            stored
            for stored in reader.strategy_versions()
            if stored.registration.strategy_version in expected
        )
        authorities = tuple(
            stored
            for stored in reader.strategy_authority_bindings()
            if stored.binding.strategy_version in expected
        )
        if (
            len(versions) != len(expected)
            or len(authorities) != len(expected)
            or any(
                reader.lifecycle_state(version, target) is None
                for version in expected
            )
        ):
            return None
        authority_by_version = {
            stored.binding.strategy_version: stored.binding
            for stored in authorities
        }
        return tuple(
            sorted(
                (
                    StrategyRegistrationIdentity(
                        strategy_version=stored.registration.strategy_version,
                        code_version=stored.registration.code_version,
                        lane_id=stored.registration.lane_id.value,
                        operating_mode=authority_by_version[
                            stored.registration.strategy_version
                        ].operating_mode.value,
                        registration_sha256=hashlib.sha256(
                            canonical_experiment_ledger_json(
                                stored.registration
                            ).encode()
                        ).hexdigest(),
                    )
                    for stored in versions
                ),
                key=lambda item: item.strategy_version,
            )
        )
    except (
        InvalidExperimentLedgerSourceError,
        UnsupportedExperimentLedgerSchemaError,
        OSError,
        ValidationError,
        ValueError,
    ):
        return None


def _ready(
    request: FutureSessionPlanRequest,
    target: dt.date,
    calendar: SessionCalendarProvenance,
    registrations: tuple[StrategyRegistrationIdentity, ...],
    jobs: tuple[JobTimingSpec, ...],
    runtime_environment: RuntimeEnvironmentAttestation,
) -> ReadyToPrepareSessionPlan:
    values = {
        "market": request.market,
        "source_request_sha256": hashlib.sha256(
            canonical_request_json(request).encode()
        ).hexdigest(),
        "target_session": target,
        "compiled_at": request.compiled_at,
        "scheduler_main_sha": request.scheduler_main_sha,
        "frozen_runtime": request.frozen_runtime,
        "calendar_provenance": calendar,
        "strategy_registrations": registrations,
        "kr_rollover_bundle_sha256": None,
        "kr_policy_sha256": None,
        "artifact_layout": FutureSessionArtifactLayout.from_root(
            request.artifact_root / request.market.value / target.isoformat()
        ),
        "trial_registration_state": (
            DeferredTrialRegistrationState.DEFERRED_UNTIL_PREOPEN
        ),
        "jobs": jobs,
        "runtime_environment": runtime_environment,
    }
    provisional = ReadyToPrepareSessionPlan.model_construct(
        plan_sha256="0" * 64,
        **values,
    )
    return ReadyToPrepareSessionPlan(
        plan_sha256=plan_content_sha256(provisional),
        **values,
    )


def _waiting(
    request: FutureSessionPlanRequest,
    target: dt.date | None,
    reason: WaitingAuthorityReason,
) -> WaitingSessionAuthority:
    return WaitingSessionAuthority(
        market=request.market,
        target_session=target,
        compiled_at=request.compiled_at,
        scheduler_main_sha=request.scheduler_main_sha,
        frozen_runtime=request.frozen_runtime,
        reasons=(reason,),
    )


def _calendar_sha256() -> str:
    payload = {
        "early_closes": sorted(day.isoformat() for day in EARLY_CLOSE_DAYS),
        "full_day_holidays": sorted(
            day.isoformat() for day in FULL_DAY_HOLIDAYS
        ),
        "years": sorted(PUBLISHED_CALENDAR_YEARS),
    }
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


__all__ = ("compile_us_future_session_plan",)
