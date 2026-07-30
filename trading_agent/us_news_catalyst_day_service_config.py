from __future__ import annotations

import hashlib
import json
import os
import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

_LABEL_CHARS: Final = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
_INTERVAL_SECONDS: Final = 30
_SERVICE_SCRIPT: Final = "run_us_news_catalyst_day_service.py"
_SESSION_SCRIPT: Final = "run_us_news_catalyst_day_session.py"


class InvalidUsNewsCatalystDayServiceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst day service is invalid"


class UsNewsCatalystDayServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    label: str
    project_root: Path
    uv_path: Path
    registration_manifest: Path
    experiment_ledger: Path
    projection_root: Path
    evidence_root: Path
    security_master_store: Path
    session_root: Path
    output_root: Path
    secret_path: Path

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        paths = tuple(value for name, value in self if name not in {"schema_version", "label"})
        label_valid = (
            bool(self.label)
            and self.label == self.label.strip(".")
            and "." in self.label
            and all(char in _LABEL_CHARS for char in self.label)
        )
        if not label_valid or any(not path.is_absolute() for path in paths):
            raise InvalidUsNewsCatalystDayServiceError
        return self


@dataclass(frozen=True, slots=True)
class UsNewsCatalystLaunchAgentVerification:
    ready: bool
    interval_seconds: int
    config_sha256: str
    plist_sha256: str


def write_us_news_catalyst_day_service_config(
    path: Path,
    config: UsNewsCatalystDayServiceConfig,
) -> bool:
    try:
        checked = UsNewsCatalystDayServiceConfig.model_validate(config.model_dump(mode="python"))
        return publish_private_immutable_text(path, _config_text(checked))
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystDayServiceError from None


def load_us_news_catalyst_day_service_config(path: Path) -> UsNewsCatalystDayServiceConfig:
    try:
        payload = read_private_text(path)
        config = UsNewsCatalystDayServiceConfig.model_validate_json(payload)
        if payload != _config_text(config):
            raise InvalidUsNewsCatalystDayServiceError
        return config
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystDayServiceError from None


def write_us_news_catalyst_launch_agent(
    path: Path,
    config: UsNewsCatalystDayServiceConfig,
    config_path: Path,
) -> bool:
    try:
        return publish_private_immutable_text(path, _launch_agent_text(config, config_path))
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValueError):
        raise InvalidUsNewsCatalystDayServiceError from None


def verify_us_news_catalyst_launch_agent(
    config_path: Path,
    plist_path: Path,
) -> UsNewsCatalystLaunchAgentVerification:
    try:
        absolute_config = config_path.expanduser().absolute()
        config_payload = read_private_text(absolute_config)
        config = load_us_news_catalyst_day_service_config(absolute_config)
        plist_payload = read_private_text(plist_path.expanduser().absolute())
        if plist_payload != _launch_agent_text(config, absolute_config):
            raise InvalidUsNewsCatalystDayServiceError
        if (
            not config.project_root.is_dir()
            or not config.uv_path.is_file()
            or not os.access(config.uv_path, os.X_OK)
            or not (config.project_root / _SERVICE_SCRIPT).is_file()
            or not (config.project_root / _SESSION_SCRIPT).is_file()
        ):
            raise InvalidUsNewsCatalystDayServiceError
        return UsNewsCatalystLaunchAgentVerification(
            ready=True,
            interval_seconds=_INTERVAL_SECONDS,
            config_sha256=hashlib.sha256(config_payload.encode()).hexdigest(),
            plist_sha256=hashlib.sha256(plist_payload.encode()).hexdigest(),
        )
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystDayServiceError from None


def _config_text(config: UsNewsCatalystDayServiceConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


def _launch_agent_text(config: UsNewsCatalystDayServiceConfig, config_path: Path) -> str:
    arguments = [
        str(config.uv_path),
        "run",
        "--offline",
        "python",
        str(config.project_root / _SERVICE_SCRIPT),
        "tick",
        "--config",
        str(config_path.expanduser().absolute()),
        "--output-dir",
        str(config.output_root / "service"),
    ]
    payload = {
        "Label": config.label,
        "ProgramArguments": arguments,
        "WorkingDirectory": str(config.project_root),
        "RunAtLoad": True,
        "StartInterval": _INTERVAL_SECONDS,
        "ThrottleInterval": _INTERVAL_SECONDS,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Umask": 0o077,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


__all__ = (
    "InvalidUsNewsCatalystDayServiceError",
    "UsNewsCatalystDayServiceConfig",
    "load_us_news_catalyst_day_service_config",
    "verify_us_news_catalyst_launch_agent",
    "write_us_news_catalyst_day_service_config",
    "write_us_news_catalyst_launch_agent",
)
