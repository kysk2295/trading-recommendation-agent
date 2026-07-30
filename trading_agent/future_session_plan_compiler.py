from __future__ import annotations

from trading_agent.future_session_kr_plan import compile_kr_future_session_plan
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    FutureSessionPlanDecision,
    FutureSessionPlanRequest,
)
from trading_agent.future_session_us_plan import compile_us_future_session_plan


def compile_future_session_plan(
    request: FutureSessionPlanRequest,
) -> FutureSessionPlanDecision:
    request = FutureSessionPlanRequest.model_validate(
        request.model_dump(mode="python")
    )
    match request.market:
        case FutureSessionMarket.US:
            return compile_us_future_session_plan(request)
        case FutureSessionMarket.KR:
            return compile_kr_future_session_plan(request)


__all__ = ("compile_future_session_plan",)
