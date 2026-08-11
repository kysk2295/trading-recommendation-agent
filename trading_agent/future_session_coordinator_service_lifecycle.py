from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from trading_agent.future_session_coordinator_inspectors import inspect_request
from trading_agent.future_session_coordinator_service_health import (
    CoordinatorClock,
    CoordinatorHealthEvaluator,
    CoordinatorSleeper,
    await_fresh_coordinator_health,
)
from trading_agent.future_session_coordinator_service_launchd import (
    LABEL,
    ServicePlistError,
    VerifiedServicePlist,
    open_verified_service_plist,
    require_verified_service_plist_identity,
)
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)
from trading_agent.future_session_coordinator_service_runtime import (
    FrozenRuntimeError,
    ensure_frozen_runtime,
)

type CoordinatorCommandRunner = Callable[[tuple[str, ...], tuple[int, ...]], int]
type CoordinatorStartResult = Literal[
    "started",
    "bootstrap_failed",
    "post_bootstrap_failed",
]

_NOT_LOADED_RETURN_CODE = 113


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
        start = _start_verified_service(verified, domain, target, runner)
        if start != "started":
            if start == "post_bootstrap_failed":
                _ = _stop_service(
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
            _ = _stop_service(
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
        if not _stop_service(target, runner, "restart_current_stop_bootout_failed"):
            return 2
        started_at = clock()
        if _start_verified_service(verified, domain, target, runner) != "started":
            _ = _stop_service(
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
            _ = _stop_service(
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
    with (
        open_verified_service_plist(current, current_path) as current_plist,
        open_verified_service_plist(candidate, candidate_path) as candidate_plist,
    ):
        if not _stop_service(target, runner, "replace_current_stop_bootout_failed"):
            return 2
        started_at = clock()
        if _start_verified_service(candidate_plist, domain, target, runner) != "started":
            return _rollback_replacement(
                current,
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
            return _rollback_replacement(
                current,
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


def _stop_service(
    target: str,
    runner: CoordinatorCommandRunner,
    failure_reason: str,
) -> bool:
    stopped = (
        runner(("/bin/launchctl", "bootout", target), ()) == 0
        or runner(("/bin/launchctl", "print", target), ()) == _NOT_LOADED_RETURN_CODE
    )
    if not stopped:
        sys.stderr.write(f"{failure_reason}\n")
    return stopped


def _start_verified_service(
    verified: VerifiedServicePlist,
    domain: str,
    target: str,
    runner: CoordinatorCommandRunner,
) -> CoordinatorStartResult:
    _ = os.lseek(verified.descriptor, 0, os.SEEK_SET)
    bootstrap = (
        "/bin/launchctl",
        "bootstrap",
        domain,
        f"/dev/fd/{verified.descriptor}",
    )
    if runner(bootstrap, (verified.descriptor,)) != 0:
        return "bootstrap_failed"
    try:
        require_verified_service_plist_identity(verified)
    except ServicePlistError:
        return "post_bootstrap_failed"
    if runner(("/bin/launchctl", "kickstart", target), ()) != 0:
        return "post_bootstrap_failed"
    if runner(("/bin/launchctl", "print", target), ()) != 0:
        return "post_bootstrap_failed"
    return "started"


def _rollback_replacement(
    current: FutureSessionCoordinatorServiceConfig,
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
    if not _stop_service(
        target,
        runner,
        "replace_candidate_cleanup_bootout_failed",
    ):
        return 2
    started_at = clock()
    if _start_verified_service(current_plist, domain, target, runner) != "started":
        sys.stderr.write("replace_current_restore_start_failed\n")
        _ = _stop_service(
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
        _ = _stop_service(
            target,
            runner,
            "replace_current_restore_cleanup_bootout_failed",
        )
    return 2


def _verify_templates(config: FutureSessionCoordinatorServiceConfig) -> None:
    _ = inspect_request(config.us_template_request_path)
    _ = inspect_request(config.kr_template_request_path)


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
