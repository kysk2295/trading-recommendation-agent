from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

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
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    FutureSessionCoordinatorServiceReport,
    FutureSessionMarketStatus,
    FutureSessionServiceResult,
    FutureSessionTickAuthority,
    canonical_service_report_json,
)
from trading_agent.future_session_coordinator_service_runtime import (
    FrozenRuntimeError,
    ensure_frozen_runtime,
)
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import (
    FrozenRuntimeAuthority,
    FutureSessionMarket,
    FutureSessionPlanRequest,
    ReadyToPrepareSessionPlan,
    WaitingSessionAuthority,
    canonical_request_json,
)
from trading_agent.future_session_us_activation_models import LaunchctlRunner
from trading_agent.future_session_us_materializer_io import write_private_file
from trading_agent.private_stable_report import write_private_stable_report

_US_ZONE = ZoneInfo("America/New_York")
_KR_ZONE = ZoneInfo("Asia/Seoul")

type LabelStatusReader = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class CoordinatorAdapters:
    launchctl_runner: LaunchctlRunner | None = None
    label_status_reader: LabelStatusReader | None = None


@dataclass(frozen=True, slots=True)
class MarketAuthority:
    config: FutureSessionCoordinatorServiceConfig
    market: FutureSessionMarket
    tick: FutureSessionTickAuthority


def planning_after_date(
    market: FutureSessionMarket,
    observed_at: dt.datetime,
) -> dt.date:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone aware")
    match market:
        case FutureSessionMarket.US:
            local = observed_at.astimezone(_US_ZONE)
            cutoff = dt.time(8, 0)
        case FutureSessionMarket.KR:
            local = observed_at.astimezone(_KR_ZONE)
            cutoff = dt.time(8, 30)
    return local.date() - dt.timedelta(days=1) if local.time() < cutoff else local.date()


def prepare_market_request(
    config: FutureSessionCoordinatorServiceConfig,
    market: FutureSessionMarket,
    authority: FutureSessionTickAuthority,
) -> tuple[FutureSessionPlanRequest, Path, Path]:
    template_path = (
        config.us_template_request_path if market is FutureSessionMarket.US else config.kr_template_request_path
    )
    template = inspect_request(template_path)
    if template.market is not market:
        raise ValueError("template market mismatch")
    context = MarketAuthority(config=config, market=market, tick=authority)
    candidate = _dynamic_request(template, context, None)
    decision = compile_future_session_plan(candidate)
    match decision:
        case ReadyToPrepareSessionPlan(target_session=target):
            pass
        case WaitingSessionAuthority(target_session=target) if target is not None:
            pass
        case WaitingSessionAuthority():
            raise ValueError("target session authority unavailable")
    request = _dynamic_request(template, context, target)
    request_path = config.state_root / "requests" / market.value / f"{target.isoformat()}.json"
    plan_path = config.state_root / "plans" / market.value / f"{target.isoformat()}.json"
    payload = canonical_request_json(request).encode()
    if request_path.exists():
        stored = inspect_request(request_path)
        dynamic_fields = {"after_date", "compiled_at"}
        if stored.model_dump(exclude=dynamic_fields) != request.model_dump(exclude=dynamic_fields):
            raise ValueError("immutable request conflict")
        return stored, request_path, plan_path
    write_private_file(request_path, payload, 0o600)
    return request, request_path, plan_path


def _dynamic_request(
    template: FutureSessionPlanRequest,
    context: MarketAuthority,
    target: dt.date | None,
) -> FutureSessionPlanRequest:
    values = template.model_dump(mode="python")
    values.update(
        after_date=planning_after_date(context.market, context.tick.observed_at),
        compiled_at=context.tick.observed_at,
        scheduler_main_sha=context.tick.scheduler_main_sha,
        authority_repository=context.config.authority_repository,
        frozen_runtime=FrozenRuntimeAuthority(
            directory=context.tick.frozen_runtime,
            commit_sha=context.tick.scheduler_main_sha,
        ),
    )
    if context.market is FutureSessionMarket.US and target is not None:
        session_root = context.config.state_root / "session-data" / "us" / target.isoformat()
        for field in ("watch_database", "opportunity_outbox", "signal_outbox"):
            original = getattr(template, field)
            if original is None:
                raise ValueError("US template path missing")
            values[field] = session_root / original.name
    return FutureSessionPlanRequest.model_validate(values)


def tick_service(
    config: FutureSessionCoordinatorServiceConfig,
    observed_at: dt.datetime,
    adapters: CoordinatorAdapters | None = None,
) -> FutureSessionCoordinatorServiceReport:
    active_adapters = CoordinatorAdapters() if adapters is None else adapters
    try:
        runtime = ensure_frozen_runtime(
            config.authority_repository,
            config.state_root / "frozen-runtimes",
        )
        commit = runtime.name
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
    except (
        FrozenRuntimeError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        blocked = _blocked_status(type(error).__name__)
        runtime = None
        commit = None
        us = blocked
        kr = blocked
    report = FutureSessionCoordinatorServiceReport(
        observed_at=observed_at,
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
        template_path = (
            config.us_template_request_path if market is FutureSessionMarket.US else config.kr_template_request_path
        )
        template = inspect_request(template_path)
        candidate = _dynamic_request(template, context, None)
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


__all__ = (
    "CoordinatorAdapters",
    "planning_after_date",
    "prepare_market_request",
    "tick_service",
)
