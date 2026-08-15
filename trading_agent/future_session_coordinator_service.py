from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from trading_agent.future_session_coordinator import coordinate_future_session
from trading_agent.future_session_coordinator_inspectors import (
    CoordinatorInspectionError,
    inspect_request,
)
from trading_agent.future_session_coordinator_models import (
    FutureSessionActivationResult,
    FutureSessionCoordinatorReceipt,
    FutureSessionCoordinatorRequest,
    FutureSessionCoordinatorResult,
    FutureSessionPreparationResult,
)
from trading_agent.future_session_coordinator_request import dynamic_request, planning_after_date
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    FutureSessionCoordinatorServiceReport,
    FutureSessionCoordinatorServiceState,
    FutureSessionMarketStatus,
    FutureSessionServiceResult,
    FutureSessionTickAuthority,
    canonical_service_config_sha256,
    canonical_service_report_json,
)
from trading_agent.future_session_coordinator_service_runtime import (
    FrozenRuntimeError,
    ensure_frozen_runtime,
)
from trading_agent.future_session_coordinator_template_authority import inspect_bound_template
from trading_agent.future_session_execution_incident_queue import (
    project_pending_execution_incidents,
)
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    FutureSessionPlanRequest,
    ReadyToPrepareSessionPlan,
    WaitingSessionAuthority,
    canonical_request_json,
)
from trading_agent.future_session_us_activation_models import LaunchctlRunner
from trading_agent.future_session_us_materializer_io import write_private_file
from trading_agent.private_stable_report import write_private_stable_report

type LabelStatusReader = Callable[[str], bool]


class FutureSessionCoordinatorServiceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CoordinatorAdapters:
    launchctl_runner: LaunchctlRunner | None = None
    label_status_reader: LabelStatusReader | None = None


@dataclass(frozen=True, slots=True)
class MarketAuthority:
    config: FutureSessionCoordinatorServiceConfig
    market: FutureSessionMarket
    tick: FutureSessionTickAuthority


def prepare_market_request(
    config: FutureSessionCoordinatorServiceConfig,
    market: FutureSessionMarket,
    authority: FutureSessionTickAuthority,
) -> tuple[FutureSessionPlanRequest, Path, Path]:
    template = inspect_bound_template(config, market)
    candidate = dynamic_request(template, config, market, authority, None)
    decision = compile_future_session_plan(candidate)
    match decision:
        case ReadyToPrepareSessionPlan(target_session=target):
            pass
        case WaitingSessionAuthority(target_session=target) if target is not None:
            pass
        case WaitingSessionAuthority():
            raise FutureSessionCoordinatorServiceError
        case unreachable:
            assert_never(unreachable)
    request = dynamic_request(template, config, market, authority, target)
    request_path = config.state_root / "requests" / market.value / f"{target.isoformat()}.json"
    plan_path = config.state_root / "plans" / market.value / f"{target.isoformat()}.json"
    payload = canonical_request_json(request).encode()
    if request_path.exists():
        stored = inspect_request(request_path)
        dynamic_fields = {"after_date", "compiled_at"}
        if stored.model_dump(exclude=dynamic_fields) != request.model_dump(exclude=dynamic_fields):
            raise FutureSessionCoordinatorServiceError
        return stored, request_path, plan_path
    write_private_file(request_path, payload, 0o600)
    return request, request_path, plan_path


def tick_service(
    config: FutureSessionCoordinatorServiceConfig,
    observed_at: dt.datetime,
    adapters: CoordinatorAdapters | None = None,
    *,
    service_started_at: dt.datetime | None = None,
) -> FutureSessionCoordinatorServiceReport:
    active_adapters = CoordinatorAdapters() if adapters is None else adapters
    started_at = observed_at if service_started_at is None else service_started_at
    failed = False
    try:
        execution_incidents = project_pending_execution_incidents(config)
        runtime = ensure_frozen_runtime(
            config.authority_repository,
            config.state_root / "frozen-runtimes",
            config.scheduler_main_sha,
            require_current_main=False,
        )
        commit = config.scheduler_main_sha
        authority = FutureSessionTickAuthority(
            observed_at=observed_at,
            scheduler_main_sha=commit,
            frozen_runtime=runtime,
        )
        us = _coordinate_market(
            MarketAuthority(config, FutureSessionMarket.US, authority),
            active_adapters,
        )
        kr = _coordinate_market(
            MarketAuthority(config, FutureSessionMarket.KR, authority),
            active_adapters,
        )
        if _status_has_incident(us, FutureSessionMarket.US, execution_incidents):
            us = _blocked_status("execution_incident")
        if _status_has_incident(kr, FutureSessionMarket.KR, execution_incidents):
            kr = _blocked_status("execution_incident")
    except (
        FrozenRuntimeError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        failed = True
        blocked = _blocked_status(type(error).__name__)
        runtime = None
        commit = None
        us = blocked
        kr = blocked
    report = FutureSessionCoordinatorServiceReport(
        config_sha256=canonical_service_config_sha256(config),
        us_template_sha256=config.us_template_sha256,
        kr_template_sha256=config.kr_template_sha256,
        service_started_at=started_at,
        observed_at=observed_at,
        service_state=(
            FutureSessionCoordinatorServiceState.FAILED if failed else FutureSessionCoordinatorServiceState.READY
        ),
        scheduler_main_sha=commit,
        frozen_runtime=runtime,
        us=us,
        kr=kr,
    )
    write_private_stable_report(
        config.state_root / "future-session-coordinator-status.json",
        canonical_service_report_json(report),
    )
    return report


def _coordinate_market(
    context: MarketAuthority,
    adapters: CoordinatorAdapters,
) -> FutureSessionMarketStatus:
    config = context.config
    market = context.market
    try:
        template = inspect_bound_template(config, market)
        candidate = dynamic_request(template, config, market, context.tick, None)
        decision = compile_future_session_plan(candidate)
        match decision:
            case WaitingSessionAuthority(target_session=None):
                receipt = FutureSessionCoordinatorReceipt(
                    result=FutureSessionCoordinatorResult.WAITING_AUTHORITY,
                    market=market,
                    target_session=None,
                    preparation=FutureSessionPreparationResult.NOT_PREPARED,
                    activation=FutureSessionActivationResult.NOT_ACTIVATED,
                    waiting_reasons=decision.reasons,
                )
                return FutureSessionMarketStatus(
                    result=FutureSessionServiceResult.WAITING_AUTHORITY,
                    receipt=receipt,
                )
            case ReadyToPrepareSessionPlan() | WaitingSessionAuthority():
                pass
            case unreachable:
                assert_never(unreachable)
        _request, request_path, plan_path = prepare_market_request(
            config,
            market,
            context.tick,
        )
        receipt = coordinate_future_session(
            FutureSessionCoordinatorRequest(
                request_path=request_path,
                plan_path=plan_path,
                launch_agents_dir=config.launch_agents_dir,
            ),
            launchctl_runner=adapters.launchctl_runner,
            label_status_reader=adapters.label_status_reader,
        )
        result = FutureSessionServiceResult(receipt.result.value)
        return FutureSessionMarketStatus(
            result=result,
            request_path=request_path,
            plan_path=plan_path,
            receipt=receipt,
        )
    except (CoordinatorInspectionError, OSError, TypeError, ValueError) as error:
        return _blocked_status(type(error).__name__)


def _blocked_status(reason: str) -> FutureSessionMarketStatus:
    return FutureSessionMarketStatus(
        result=FutureSessionServiceResult.BLOCKED,
        reason=reason,
    )


def _status_has_incident(
    status: FutureSessionMarketStatus,
    market: FutureSessionMarket,
    incidents: frozenset[tuple[FutureSessionMarket, dt.date]],
) -> bool:
    target = None if status.receipt is None else status.receipt.target_session
    return target is not None and (market, target) in incidents


__all__ = (
    "CoordinatorAdapters",
    "FutureSessionCoordinatorServiceError",
    "planning_after_date",
    "prepare_market_request",
    "tick_service",
)
