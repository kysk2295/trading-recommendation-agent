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

_INTERVAL_SECONDS: Final = 900
_LABEL_CHARS: Final = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
_SCRIPT: Final = "run_alpaca_indicative_research.py"


class InvalidIndicativeResearchServiceError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca indicative research service is invalid"


class IndicativeResearchServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    label: str
    project_root: Path
    uv_path: Path
    outputs_root: Path
    credentials_path: Path
    report_root: Path

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        paths = tuple(value for name, value in self if name not in {"schema_version", "label"})
        label_valid = (
            bool(self.label)
            and self.label == self.label.strip(".")
            and "." in self.label
            and all(character in _LABEL_CHARS for character in self.label)
        )
        if not label_valid or any(not path.is_absolute() for path in paths):
            raise InvalidIndicativeResearchServiceError
        return self


@dataclass(frozen=True, slots=True)
class IndicativeResearchLaunchAgentVerification:
    ready: bool
    interval_seconds: int
    config_sha256: str
    plist_sha256: str


def write_indicative_research_service_config(path: Path, config: IndicativeResearchServiceConfig) -> bool:
    try:
        checked = IndicativeResearchServiceConfig.model_validate(config.model_dump(mode="python"))
        return publish_private_immutable_text(path, _config_text(checked))
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidIndicativeResearchServiceError from None


def load_indicative_research_service_config(path: Path) -> IndicativeResearchServiceConfig:
    try:
        payload = read_private_text(path)
        config = IndicativeResearchServiceConfig.model_validate_json(payload)
        if payload != _config_text(config):
            raise InvalidIndicativeResearchServiceError
        return config
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidIndicativeResearchServiceError from None


def write_indicative_research_launch_agent(
    path: Path,
    config: IndicativeResearchServiceConfig,
    config_path: Path,
) -> bool:
    try:
        return publish_private_immutable_text(path, _launch_agent_text(config, config_path))
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValueError):
        raise InvalidIndicativeResearchServiceError from None


def verify_indicative_research_launch_agent(
    config_path: Path,
    plist_path: Path,
) -> IndicativeResearchLaunchAgentVerification:
    try:
        absolute_config = config_path.expanduser().absolute()
        config_payload = read_private_text(absolute_config)
        config = load_indicative_research_service_config(absolute_config)
        plist_payload = read_private_text(plist_path.expanduser().absolute())
        if plist_payload != _launch_agent_text(config, absolute_config):
            raise InvalidIndicativeResearchServiceError
        if (
            not config.project_root.is_dir()
            or not config.uv_path.is_file()
            or not os.access(config.uv_path, os.X_OK)
            or not (config.project_root / _SCRIPT).is_file()
        ):
            raise InvalidIndicativeResearchServiceError
        return IndicativeResearchLaunchAgentVerification(
            ready=True,
            interval_seconds=_INTERVAL_SECONDS,
            config_sha256=hashlib.sha256(config_payload.encode()).hexdigest(),
            plist_sha256=hashlib.sha256(plist_payload.encode()).hexdigest(),
        )
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidIndicativeResearchServiceError from None


def _config_text(config: IndicativeResearchServiceConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


def _launch_agent_text(config: IndicativeResearchServiceConfig, config_path: Path) -> str:
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
            "--output-dir",
            str(config.report_root),
        ],
        "WorkingDirectory": str(config.project_root),
        "RunAtLoad": True,
        "StartInterval": _INTERVAL_SECONDS,
        "ThrottleInterval": 60,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Umask": 0o077,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


__all__ = (
    "IndicativeResearchLaunchAgentVerification",
    "IndicativeResearchServiceConfig",
    "InvalidIndicativeResearchServiceError",
    "load_indicative_research_service_config",
    "verify_indicative_research_launch_agent",
    "write_indicative_research_launch_agent",
    "write_indicative_research_service_config",
)
