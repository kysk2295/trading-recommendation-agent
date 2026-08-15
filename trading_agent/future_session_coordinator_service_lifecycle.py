from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from trading_agent.future_session_coordinator_child_inventory import require_no_loaded_child_jobs
from trading_agent.future_session_coordinator_launchd_transaction import (
    CoordinatorCommandRunner,
    start_verified_service,
    stop_service,
)
from trading_agent.future_session_coordinator_replacement import rollback_replacement
from trading_agent.future_session_coordinator_service_health import (
    CoordinatorClock,
    CoordinatorHealthEvaluator,
    CoordinatorSleeper,
    await_fresh_coordinator_health,
)
from trading_agent.future_session_coordinator_service_launchd import (
    LABEL,
    open_verified_service_plist,
)
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)
from trading_agent.future_session_coordinator_service_runtime import (
    FrozenRuntimeError,
    ensure_frozen_runtime,
)
from trading_agent.future_session_coordinator_template_authority import (
    FutureSessionTemplateAuthorityError,
    verify_bound_templates,
)


def verify_coordinator_authority(config: FutureSessionCoordinatorServiceConfig) -> None:
    _verify_templates(config)
    runtime = ensure_frozen_runtime(
        config.authority_repository,
        config.state_root / "frozen-runtimes",
        config.scheduler_main_sha,
    )
    _verify_runtime_entrypoint(runtime)


def verify_frozen_coordinator_authority(
    config: FutureSessionCoordinatorServiceConfig,
) -> None:
    _verify_templates(config)
    runtime = ensure_frozen_runtime(
        config.authority_repository,
        config.state_root / "frozen-runtimes",
        config.scheduler_main_sha,
        require_current_main=False,
    )
    _verify_runtime_entrypoint(runtime)


def activate_coordinator_service(
    config: FutureSessionCoordinatorServiceConfig,
    config_path: Path,
    runner: CoordinatorCommandRunner,
    clock: CoordinatorClock,
    health_evaluator: CoordinatorHealthEvaluator,
    sleeper: CoordinatorSleeper,
) -> int:
    verify_coordinator_authority(config)
    with open_verified_service_plist(config, config_path) as verified:
        domain = f"gui/{os.getuid()}"
        target = f"{domain}/{LABEL}"
        started_at = clock()
        start = start_verified_service(verified, domain, target, runner)
        if start != "started":
            if start == "post_bootstrap_failed":
                _ = stop_service(
                    target,
                    runner,
                    "activate_cleanup_bootout_failed",
                )
            return 2
        health = await_fresh_coordinator_health(
            config,
            started_at,
            clock,
            health_evaluator,
            sleeper,
        )
        if not health.accepted:
            sys.stderr.write(f"activate_health_{health.reason}\n")
            _ = stop_service(
                target,
                runner,
                "activate_cleanup_bootout_failed",
            )
            return 2
    return 0


def restart_coordinator_service(
    config: FutureSessionCoordinatorServiceConfig,
    config_path: Path,
    runner: CoordinatorCommandRunner,
    clock: CoordinatorClock,
    health_evaluator: CoordinatorHealthEvaluator,
    sleeper: CoordinatorSleeper,
) -> int:
    verify_frozen_coordinator_authority(config)
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    with open_verified_service_plist(config, config_path) as verified:
        if not stop_service(target, runner, "restart_current_stop_bootout_failed"):
            return 2
        started_at = clock()
        if start_verified_service(verified, domain, target, runner) != "started":
            _ = stop_service(
                target,
                runner,
                "restart_cleanup_bootout_failed",
            )
            return 2
        health = await_fresh_coordinator_health(
            config,
            started_at,
            clock,
            health_evaluator,
            sleeper,
        )
        if not health.accepted:
            sys.stderr.write(f"restart_health_{health.reason}\n")
            _ = stop_service(
                target,
                runner,
                "restart_cleanup_bootout_failed",
            )
            return 2
    return 0


def replace_coordinator_service(
    current: FutureSessionCoordinatorServiceConfig,
    current_path: Path,
    candidate: FutureSessionCoordinatorServiceConfig,
    candidate_path: Path,
    runner: CoordinatorCommandRunner,
    clock: CoordinatorClock,
    health_evaluator: CoordinatorHealthEvaluator,
    sleeper: CoordinatorSleeper,
) -> int:
    if current.authority_repository != candidate.authority_repository:
        raise FrozenRuntimeError("replacement_authority_mismatch")
    verify_frozen_coordinator_authority(current)
    verify_coordinator_authority(candidate)
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    if not require_no_loaded_child_jobs(current, domain, runner):
        return 2
    with (
        open_verified_service_plist(current, current_path) as current_plist,
        open_verified_service_plist(candidate, candidate_path) as candidate_plist,
    ):
        if not stop_service(target, runner, "replace_current_stop_bootout_failed"):
            return 2
        if not require_no_loaded_child_jobs(current, domain, runner):
            return rollback_replacement(
                current,
                None,
                current_plist,
                domain,
                target,
                runner,
                clock,
                health_evaluator,
                sleeper,
                "child_inventory_changed",
            )
        started_at = clock()
        if start_verified_service(candidate_plist, domain, target, runner) != "started":
            return rollback_replacement(
                current,
                candidate,
                current_plist,
                domain,
                target,
                runner,
                clock,
                health_evaluator,
                sleeper,
                "candidate_start_failed",
            )
        health = await_fresh_coordinator_health(
            candidate,
            started_at,
            clock,
            health_evaluator,
            sleeper,
        )
        if not health.accepted:
            return rollback_replacement(
                current,
                candidate,
                current_plist,
                domain,
                target,
                runner,
                clock,
                health_evaluator,
                sleeper,
                f"health_{health.reason}",
            )
    return 0


def _verify_templates(config: FutureSessionCoordinatorServiceConfig) -> None:
    try:
        verify_bound_templates(config)
    except FutureSessionTemplateAuthorityError:
        raise FrozenRuntimeError("template_authority_mismatch") from None


def _verify_runtime_entrypoint(runtime: Path) -> None:
    entrypoint = runtime / "run_future_session_coordinator_service.py"
    metadata = entrypoint.lstat()
    if (
        entrypoint.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
    ):
        raise FrozenRuntimeError("frozen_runtime_entrypoint_invalid")


__all__ = (
    "CoordinatorCommandRunner",
    "activate_coordinator_service",
    "replace_coordinator_service",
    "restart_coordinator_service",
    "verify_coordinator_authority",
    "verify_frozen_coordinator_authority",
)
