from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

US_DAY_SESSION_LABEL: Final = "ai.trading-agent.us-day-session"
KR_DAY_SESSION_LABEL: Final = "ai.trading-agent.kr-day-session"
DAY_SESSION_INTERVAL_SECONDS: Final = 120
_SCRIPT: Final = "run_day_session_service.py"
_SHA: Final = re.compile(r"[a-f0-9]{40}")


class InvalidDaySessionServiceError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _CommonConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    project_root: Path
    expected_commit: str
    uv_path: Path
    source_root: Path
    state_root: Path

    @model_validator(mode="after")
    def require_absolute_bindings(self) -> Self:
        paths = (self.project_root, self.uv_path, self.source_root, self.state_root)
        if any(not path.is_absolute() for path in paths) or _SHA.fullmatch(self.expected_commit) is None:
            raise InvalidDaySessionServiceError(reason="service_binding_invalid")
        return self


class UsDaySessionServiceConfig(_CommonConfig):
    market: Literal["us"] = "us"
    label: Literal["ai.trading-agent.us-day-session"] = US_DAY_SESSION_LABEL
    live_model_provider: Literal["claude-code"] = "claude-code"


class KrDaySessionServiceConfig(_CommonConfig):
    market: Literal["kr"] = "kr"
    label: Literal["ai.trading-agent.kr-day-session"] = KR_DAY_SESSION_LABEL
    calendar_store: Path
    experiment_ledger: Path
    hermes_delivery_database: Path

    @model_validator(mode="after")
    def require_kr_absolute_bindings(self) -> Self:
        if (
            not self.calendar_store.is_absolute()
            or not self.experiment_ledger.is_absolute()
            or not self.hermes_delivery_database.is_absolute()
        ):
            raise InvalidDaySessionServiceError(reason="service_binding_invalid")
        return self


DaySessionServiceConfig = Annotated[
    UsDaySessionServiceConfig | KrDaySessionServiceConfig,
    Field(discriminator="market"),
]
_CONFIG = TypeAdapter(DaySessionServiceConfig)


@dataclass(frozen=True, slots=True)
class DaySessionLaunchAgentVerification:
    ready: bool
    interval_seconds: int
    config_sha256: str
    plist_sha256: str


@dataclass(frozen=True, slots=True)
class DaySessionVersionedPaths:
    config: Path
    plist: Path


def write_day_session_service_config(path: Path, config: DaySessionServiceConfig) -> bool:
    try:
        checked = _CONFIG.validate_python(config)
        if checked.expected_commit not in path.name:
            raise InvalidDaySessionServiceError(reason="service_config_not_versioned")
        return publish_private_immutable_text(path, _config_text(checked))
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidDaySessionServiceError(reason="service_config_write_invalid") from None


def load_day_session_service_config(path: Path) -> DaySessionServiceConfig:
    try:
        payload = read_private_text(path.expanduser().absolute())
        config = _CONFIG.validate_json(payload)
        if payload != _config_text(config):
            raise InvalidDaySessionServiceError(reason="service_config_noncanonical")
        return config
    except (
        InvalidDaySessionServiceError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidDaySessionServiceError(reason="service_config_read_invalid") from None


def write_day_session_launch_agent(
    path: Path,
    config: DaySessionServiceConfig,
    config_path: Path,
) -> bool:
    try:
        return publish_private_immutable_text(path, _plist_text(config, config_path))
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValueError):
        raise InvalidDaySessionServiceError(reason="launch_agent_write_invalid") from None


def verify_day_session_launch_agent(
    config_path: Path,
    plist_path: Path,
) -> DaySessionLaunchAgentVerification:
    try:
        absolute_config = config_path.expanduser().absolute()
        config_text = read_private_text(absolute_config)
        config = load_day_session_service_config(absolute_config)
        plist_text = read_private_text(plist_path.expanduser().absolute())
        if plist_text != _plist_text(config, absolute_config):
            raise InvalidDaySessionServiceError(reason="launch_agent_contract_invalid")
        required = (config.uv_path, config.project_root / _SCRIPT)
        source_contract = _source_contract_paths(config)
        if (
            not config.project_root.is_dir()
            or any(not path.is_file() for path in required)
            or not os.access(config.uv_path, os.X_OK)
            or any(not path.is_dir() for path in source_contract)
        ):
            raise InvalidDaySessionServiceError(reason="service_executable_binding_invalid")
        return DaySessionLaunchAgentVerification(
            ready=True,
            interval_seconds=DAY_SESSION_INTERVAL_SECONDS,
            config_sha256=hashlib.sha256(config_text.encode()).hexdigest(),
            plist_sha256=hashlib.sha256(plist_text.encode()).hexdigest(),
        )
    except (
        InvalidDaySessionServiceError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidDaySessionServiceError(reason="launch_agent_verify_invalid") from None


def versioned_day_session_paths(
    config_directory: Path,
    launch_agents_directory: Path,
    market: Literal["us", "kr"],
    expected_commit: str,
) -> DaySessionVersionedPaths:
    if (
        not config_directory.is_absolute()
        or not launch_agents_directory.is_absolute()
        or _SHA.fullmatch(expected_commit) is None
    ):
        raise InvalidDaySessionServiceError(reason="service_version_path_invalid")
    stem = f"{market}-day-session-v2-{expected_commit}"
    return DaySessionVersionedPaths(
        config=config_directory / f"{stem}.json",
        plist=launch_agents_directory / f"ai.trading-agent.{stem}.plist",
    )


LaunchctlRunner = Callable[[tuple[str, ...]], int]


def replace_day_session_launch_agent(
    current_config: Path,
    current_plist: Path,
    candidate_config: Path,
    candidate_plist: Path,
    *,
    runner: LaunchctlRunner | None = None,
) -> bool:
    current = load_day_session_service_config(current_config)
    candidate = load_day_session_service_config(candidate_config)
    _ = verify_day_session_launch_agent(current_config, current_plist)
    _ = verify_day_session_launch_agent(candidate_config, candidate_plist)
    if (
        current.market != candidate.market
        or current.label != candidate.label
        or current.project_root != candidate.project_root
        or current.expected_commit == candidate.expected_commit
    ):
        raise InvalidDaySessionServiceError(reason="service_cutover_binding_invalid")
    active = _launchctl if runner is None else runner
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{candidate.label}"
    old = str(current_plist.expanduser().absolute())
    new = str(candidate_plist.expanduser().absolute())
    if active(("/bin/launchctl", "bootout", domain, old)) != 0:
        raise InvalidDaySessionServiceError(reason="service_cutover_bootout_invalid")
    if active(("/bin/launchctl", "bootstrap", domain, new)) == 0 and active(
        ("/bin/launchctl", "kickstart", target)
    ) == 0:
        return True
    _ = active(("/bin/launchctl", "bootout", domain, new))
    if active(("/bin/launchctl", "bootstrap", domain, old)) != 0:
        raise InvalidDaySessionServiceError(reason="service_cutover_rollback_invalid")
    _ = active(("/bin/launchctl", "kickstart", target))
    return False


def _source_contract_paths(config: DaySessionServiceConfig) -> tuple[Path, ...]:
    match config:
        case UsDaySessionServiceConfig():
            return (config.source_root,)
        case KrDaySessionServiceConfig():
            return (
                config.source_root,
                config.calendar_store.parent,
                config.experiment_ledger.parent,
                config.hermes_delivery_database.parent,
            )


def _launchctl(command: tuple[str, ...]) -> int:
    return subprocess.run(
        command,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


def _config_text(config: DaySessionServiceConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), separators=(",", ":"), sort_keys=True) + "\n"


def _plist_text(config: DaySessionServiceConfig, config_path: Path) -> str:
    payload = {
        "Label": config.label,
        "ProgramArguments": [
            str(config.uv_path),
            "run",
            "--offline",
            "python",
            str(config.project_root / _SCRIPT),
            "tick",
            "--config",
            str(config_path.expanduser().absolute()),
        ],
        "WorkingDirectory": str(config.project_root),
        "RunAtLoad": True,
        "StartInterval": DAY_SESSION_INTERVAL_SECONDS,
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Umask": 0o077,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode()


__all__ = (
    "DAY_SESSION_INTERVAL_SECONDS",
    "KR_DAY_SESSION_LABEL",
    "US_DAY_SESSION_LABEL",
    "DaySessionServiceConfig",
    "DaySessionVersionedPaths",
    "InvalidDaySessionServiceError",
    "KrDaySessionServiceConfig",
    "UsDaySessionServiceConfig",
    "load_day_session_service_config",
    "replace_day_session_launch_agent",
    "verify_day_session_launch_agent",
    "versioned_day_session_paths",
    "write_day_session_launch_agent",
    "write_day_session_service_config",
)
