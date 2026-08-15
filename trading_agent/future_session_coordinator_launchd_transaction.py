from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Literal

from trading_agent.future_session_coordinator_service_launchd import (
    ServicePlistError,
    VerifiedServicePlist,
    require_verified_service_plist_identity,
)

type CoordinatorCommandRunner = Callable[[tuple[str, ...], tuple[int, ...]], int]
type CoordinatorStartResult = Literal[
    "started",
    "bootstrap_failed",
    "post_bootstrap_failed",
]

_NOT_LOADED_RETURN_CODE = 113


def stop_service(
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


def start_verified_service(
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


__all__ = (
    "CoordinatorCommandRunner",
    "CoordinatorStartResult",
    "start_verified_service",
    "stop_service",
)
