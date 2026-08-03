from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.research_agent_soak_models import SoakObservation

_LINUX_BOOT_ID: Final = Path("/proc/sys/kernel/random/boot_id")


@dataclass(frozen=True, slots=True)
class InvalidSoakRuntimeIdentityError(RuntimeError):
    reason: str

    @override
    def __str__(self) -> str:
        return "research-agent soak runtime identity is unavailable"


def capture_soak_observation() -> SoakObservation:
    return SoakObservation(
        recorded_at=dt.datetime.now(dt.UTC),
        monotonic_ns=time.monotonic_ns(),
        boot_sha256=hashlib.sha256(_boot_identity()).hexdigest(),
        invocation_sha256=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
    )


def current_utc_time() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _boot_identity() -> bytes:
    if _LINUX_BOOT_ID.is_file():
        try:
            return _LINUX_BOOT_ID.read_bytes().strip()
        except OSError:
            raise InvalidSoakRuntimeIdentityError(reason="boot_identity_read_failed") from None
    try:
        result = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            check=True,
            capture_output=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        raise InvalidSoakRuntimeIdentityError(reason="boot_identity_read_failed") from None
    if not result.stdout:
        raise InvalidSoakRuntimeIdentityError(reason="boot_identity_empty")
    return result.stdout.strip()


__all__ = ("InvalidSoakRuntimeIdentityError", "capture_soak_observation", "current_utc_time")
