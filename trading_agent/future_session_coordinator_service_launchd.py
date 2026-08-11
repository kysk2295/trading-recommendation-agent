from __future__ import annotations

import os
import plistlib
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)
from trading_agent.future_session_us_materializer_io import write_private_file

LABEL: Final = "ai.trading-agent.future-session-coordinator"


@dataclass(frozen=True, slots=True)
class ServicePlistError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


def service_plist_path(config: FutureSessionCoordinatorServiceConfig) -> Path:
    return config.launch_agents_dir / f"{LABEL}.plist"


def canonical_service_plist(
    config: FutureSessionCoordinatorServiceConfig,
    config_path: Path,
) -> bytes:
    logs = config.state_root / "logs"
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            str(config.authority_repository / "run_future_session_coordinator_service.py"),
            "run",
            "--config",
            str(config_path),
        ],
        "KeepAlive": True,
        "RunAtLoad": True,
        "StandardOutPath": str(logs / "coordinator.stdout.log"),
        "StandardErrorPath": str(logs / "coordinator.stderr.log"),
        "WorkingDirectory": str(config.authority_repository),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def provision_service_plist(
    config: FutureSessionCoordinatorServiceConfig,
    config_path: Path,
) -> Path:
    destination = service_plist_path(config)
    expected = canonical_service_plist(config, config_path)
    if destination.exists():
        verify_service_plist(config, config_path)
        return destination
    (config.state_root / "logs").mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(config.state_root / "logs", 0o700)
    write_private_file(destination, expected, 0o600)
    return destination


def verify_service_plist(
    config: FutureSessionCoordinatorServiceConfig,
    config_path: Path,
) -> Path:
    destination = service_plist_path(config)
    try:
        metadata = destination.lstat()
        payload = destination.read_bytes()
    except OSError:
        raise ServicePlistError("service_plist_missing") from None
    if (
        destination.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or payload != canonical_service_plist(config, config_path)
    ):
        raise ServicePlistError("service_plist_invalid")
    return destination


__all__ = (
    "LABEL",
    "ServicePlistError",
    "canonical_service_plist",
    "provision_service_plist",
    "service_plist_path",
    "verify_service_plist",
)
