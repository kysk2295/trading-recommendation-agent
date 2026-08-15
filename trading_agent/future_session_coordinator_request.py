from __future__ import annotations

import datetime as dt
from typing import assert_never
from zoneinfo import ZoneInfo

from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    FutureSessionTickAuthority,
)
from trading_agent.future_session_plan_models import (
    FrozenRuntimeAuthority,
    FutureSessionMarket,
    FutureSessionPlanRequest,
)

_US_ZONE = ZoneInfo("America/New_York")
_KR_ZONE = ZoneInfo("Asia/Seoul")


class FutureSessionCoordinatorRequestError(ValueError):
    pass


def planning_after_date(
    market: FutureSessionMarket,
    observed_at: dt.datetime,
) -> dt.date:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise FutureSessionCoordinatorRequestError
    match market:
        case FutureSessionMarket.US:
            local = observed_at.astimezone(_US_ZONE)
            cutoff = dt.time(8, 0)
        case FutureSessionMarket.KR:
            local = observed_at.astimezone(_KR_ZONE)
            cutoff = dt.time(8, 30)
        case unreachable:
            assert_never(unreachable)
    return local.date() - dt.timedelta(days=1) if local.time() < cutoff else local.date()


def dynamic_request(
    template: FutureSessionPlanRequest,
    config: FutureSessionCoordinatorServiceConfig,
    market: FutureSessionMarket,
    authority: FutureSessionTickAuthority,
    target: dt.date | None,
) -> FutureSessionPlanRequest:
    values = template.model_dump(mode="python")
    values.update(
        after_date=planning_after_date(market, authority.observed_at),
        compiled_at=authority.observed_at,
        scheduler_main_sha=authority.scheduler_main_sha,
        scheduler_authority_mode="frozen_runtime",
        authority_repository=config.authority_repository,
        artifact_root=config.state_root / "artifacts",
        frozen_runtime=FrozenRuntimeAuthority(
            directory=authority.frozen_runtime,
            commit_sha=authority.scheduler_main_sha,
        ),
    )
    if market is FutureSessionMarket.US:
        target_name = "pending-target" if target is None else target.isoformat()
        session_root = authority.frozen_runtime / "outputs" / "future-sessions" / "us" / target_name
        values["watch_database"] = session_root / "paper_recommendations.sqlite3"
        values["opportunity_outbox"] = session_root / "opportunities.v1.jsonl"
        values["signal_outbox"] = session_root / "trade-signals.v1.jsonl"
    elif market is not FutureSessionMarket.KR:
        assert_never(market)
    return FutureSessionPlanRequest.model_validate(values)


__all__ = (
    "FutureSessionCoordinatorRequestError",
    "dynamic_request",
    "planning_after_date",
)
