from __future__ import annotations

import hashlib
import os
import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.kr_day_close_service_config import (
    KrDayCloseServiceConfig,
    load_kr_day_close_service_config,
    require_kr_day_close_service_authority,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

KR_DAY_CLOSE_LABEL: Final = "ai.trading-agent.kr-day-close"
KR_DAY_CLOSE_INVOCATIONS: Final = ((15, 40), (16, 10), (18, 0))


class InvalidKrDayCloseLaunchAgentError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day-close LaunchAgent is invalid"


@dataclass(frozen=True, slots=True)
class KrDayCloseLaunchAgentVerification:
    ready: bool
    invocation_count: int
    plist_sha256: str


def kr_day_close_launch_agent_path(config: KrDayCloseServiceConfig) -> Path:
    return config.launch_agents_directory / f"{KR_DAY_CLOSE_LABEL}-{config.expected_commit}.plist"


def canonical_kr_day_close_launch_agent(
    config: KrDayCloseServiceConfig,
    config_path: Path,
) -> bytes:
    payload = {
        "Label": KR_DAY_CLOSE_LABEL,
        "ProgramArguments": [
            str(config.executable_path),
            str(config.project_root / "run_kr_day_close_service.py"),
            "--config",
            str(config_path),
        ],
        "RunAtLoad": False,
        "StartCalendarInterval": [
            {"Hour": hour, "Minute": minute} for hour, minute in KR_DAY_CLOSE_INVOCATIONS
        ],
        "StandardOutPath": str(config.health_root / "kr-day-close.stdout.log"),
        "StandardErrorPath": str(config.health_root / "kr-day-close.stderr.log"),
        "WorkingDirectory": str(config.project_root),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def provision_kr_day_close_launch_agent(
    config: KrDayCloseServiceConfig,
    config_path: Path,
) -> Path:
    try:
        config.health_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(config.health_root, 0o700)
        path = kr_day_close_launch_agent_path(config)
        _ = publish_private_immutable_text(
            path,
            canonical_kr_day_close_launch_agent(config, config_path).decode(),
        )
        _ = verify_kr_day_close_launch_agent(config, config_path)
        return path
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValueError):
        raise InvalidKrDayCloseLaunchAgentError from None


def verify_kr_day_close_launch_agent(
    config: KrDayCloseServiceConfig,
    config_path: Path,
) -> KrDayCloseLaunchAgentVerification:
    try:
        if not config_path.is_absolute() or config_path.is_symlink():
            raise InvalidKrDayCloseLaunchAgentError
        if load_kr_day_close_service_config(config_path) != config:
            raise InvalidKrDayCloseLaunchAgentError
        require_kr_day_close_service_authority(config)
        path = kr_day_close_launch_agent_path(config)
        payload = read_private_text(path).encode()
        expected = canonical_kr_day_close_launch_agent(config, config_path)
        parsed = plistlib.loads(payload)
        intervals = parsed["StartCalendarInterval"]
        if (
            payload != expected
            or len(intervals) < 2
            or any("Weekday" in interval for interval in intervals)
            or parsed["ProgramArguments"][2:] != ["--config", str(config_path)]
        ):
            raise InvalidKrDayCloseLaunchAgentError
        return KrDayCloseLaunchAgentVerification(
            ready=True,
            invocation_count=len(intervals),
            plist_sha256=hashlib.sha256(payload).hexdigest(),
        )
    except (
        InvalidKrDayCloseLaunchAgentError,
        InvalidPrivateImmutableFileError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise InvalidKrDayCloseLaunchAgentError from None


__all__ = (
    "KR_DAY_CLOSE_INVOCATIONS",
    "KR_DAY_CLOSE_LABEL",
    "InvalidKrDayCloseLaunchAgentError",
    "KrDayCloseLaunchAgentVerification",
    "canonical_kr_day_close_launch_agent",
    "kr_day_close_launch_agent_path",
    "provision_kr_day_close_launch_agent",
    "verify_kr_day_close_launch_agent",
)
