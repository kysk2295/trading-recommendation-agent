from __future__ import annotations

from trading_agent.future_session_kr_plan import compile_kr_future_session_plan
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    FutureSessionPlanDecision,
    FutureSessionPlanRequest,
    WaitingAuthorityReason,
    WaitingSessionAuthority,
)
from trading_agent.future_session_us_activation_models import (
    FutureSessionActivationError,
)
from trading_agent.future_session_us_activation_verifier import verify_frozen_runtime
from trading_agent.future_session_us_plan import compile_us_future_session_plan
from trading_agent.repository_current_main import (
    CurrentMainAuthorityError,
    current_main_commit,
)


def compile_future_session_plan(
    request: FutureSessionPlanRequest,
) -> FutureSessionPlanDecision:
    request = FutureSessionPlanRequest.model_validate(request.model_dump(mode="python"))
    if not _scheduler_authority_is_valid(request):
        return _scheduler_waiting(request)
    match request.market:
        case FutureSessionMarket.US:
            return compile_us_future_session_plan(request)
        case FutureSessionMarket.KR:
            return compile_kr_future_session_plan(request)


def _scheduler_authority_is_valid(request: FutureSessionPlanRequest) -> bool:
    if request.scheduler_authority_mode == "frozen_runtime":
        if request.frozen_runtime.commit_sha != request.scheduler_main_sha:
            return False
        try:
            verify_frozen_runtime(
                request.frozen_runtime.directory,
                request.scheduler_main_sha,
            )
        except (FutureSessionActivationError, OSError, TypeError, ValueError):
            return False
        return True
    try:
        scheduler_main_sha = current_main_commit(request.authority_repository)
    except CurrentMainAuthorityError:
        return False
    return scheduler_main_sha == request.scheduler_main_sha


def _scheduler_waiting(
    request: FutureSessionPlanRequest,
) -> FutureSessionPlanDecision:
    return WaitingSessionAuthority(
        market=request.market,
        target_session=None,
        compiled_at=request.compiled_at,
        scheduler_main_sha=request.scheduler_main_sha,
        frozen_runtime=request.frozen_runtime,
        reasons=(WaitingAuthorityReason.SCHEDULER_AUTHORITY_INVALID,),
    )


__all__ = ("compile_future_session_plan",)
