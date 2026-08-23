from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
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


def write_day_session_service_config(path: Path, config: DaySessionServiceConfig) -> bool:
    try:
        checked = _CONFIG.validate_python(config)
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
        if (
            not config.project_root.is_dir()
            or any(not path.is_file() for path in required)
            or not os.access(config.uv_path, os.X_OK)
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
    "InvalidDaySessionServiceError",
    "KrDaySessionServiceConfig",
    "UsDaySessionServiceConfig",
    "load_day_session_service_config",
    "verify_day_session_launch_agent",
    "write_day_session_launch_agent",
    "write_day_session_service_config",
)
