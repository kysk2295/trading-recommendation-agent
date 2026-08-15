from __future__ import annotations

import sys

from trading_agent.future_session_coordinator_child_inventory import cleanup_owned_child_jobs
from trading_agent.future_session_coordinator_launchd_transaction import (
    CoordinatorCommandRunner,
    start_verified_service,
    stop_service,
)
from trading_agent.future_session_coordinator_service_health import (
    CoordinatorClock,
    CoordinatorHealthEvaluator,
    CoordinatorSleeper,
    await_fresh_coordinator_health,
)
from trading_agent.future_session_coordinator_service_launchd import VerifiedServicePlist
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)


def rollback_replacement(
    current: FutureSessionCoordinatorServiceConfig,
    candidate: FutureSessionCoordinatorServiceConfig | None,
    current_plist: VerifiedServicePlist,
    domain: str,
    target: str,
    runner: CoordinatorCommandRunner,
    clock: CoordinatorClock,
    health_evaluator: CoordinatorHealthEvaluator,
    sleeper: CoordinatorSleeper,
    reason: str,
) -> int:
    sys.stderr.write(f"replace_{reason}\n")
    if not stop_service(
        target,
        runner,
        "replace_candidate_cleanup_bootout_failed",
    ):
        return 2
    if candidate is not None and not cleanup_owned_child_jobs(candidate, domain, runner):
        return 2
    started_at = clock()
    if start_verified_service(current_plist, domain, target, runner) != "started":
        sys.stderr.write("replace_current_restore_start_failed\n")
        _ = stop_service(
            target,
            runner,
            "replace_current_restore_cleanup_bootout_failed",
        )
        return 2
    health = await_fresh_coordinator_health(
        current,
        started_at,
        clock,
        health_evaluator,
        sleeper,
    )
    if not health.accepted:
        sys.stderr.write(f"replace_current_restore_health_{health.reason}\n")
        _ = stop_service(
            target,
            runner,
            "replace_current_restore_cleanup_bootout_failed",
        )
    return 2


__all__ = ("rollback_replacement",)
