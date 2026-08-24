from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

_SHA = re.compile(r"^[0-9a-f]{40}$")


class InvalidKrDayCloseServiceConfigError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day-close service config is invalid"


class KrDayCloseServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    project_root: Path
    expected_commit: str
    executable_path: Path
    state_root: Path
    calendar_store: Path
    experiment_ledger: Path
    report_root: Path
    policy_root: Path
    hermes_delivery_database: Path
    health_root: Path
    completion_root: Path
    launch_agents_directory: Path

    @model_validator(mode="after")
    def require_coherent_private_bindings(self) -> Self:
        paths = (
            self.project_root,
            self.executable_path,
            self.state_root,
            self.calendar_store,
            self.experiment_ledger,
            self.report_root,
            self.policy_root,
            self.hermes_delivery_database,
            self.health_root,
            self.completion_root,
            self.launch_agents_directory,
        )
        service_owned_paths = (self.report_root, self.policy_root, self.health_root, self.completion_root)
        if (
            any(not path.is_absolute() or path.is_symlink() for path in paths)
            or _SHA.fullmatch(self.expected_commit) is None
            or any(not path.is_relative_to(self.state_root) for path in service_owned_paths)
            or len(set(service_owned_paths)) != len(service_owned_paths)
        ):
            raise InvalidKrDayCloseServiceConfigError
        return self

    @property
    def decision_store(self) -> Path:
        return self.state_root / "kr-day-decisions.sqlite3"

    @property
    def shadow_store(self) -> Path:
        return self.state_root / "kr-day-capsule-shadow.sqlite3"


def canonical_kr_day_close_service_config(config: KrDayCloseServiceConfig) -> str:
    checked = KrDayCloseServiceConfig.model_validate(config.model_dump(mode="python"))
    return json.dumps(checked.model_dump(mode="json"), separators=(",", ":"), sort_keys=True) + "\n"


def kr_day_close_service_config_sha256(config: KrDayCloseServiceConfig) -> str:
    return hashlib.sha256(canonical_kr_day_close_service_config(config).encode()).hexdigest()


def write_kr_day_close_service_config(path: Path, config: KrDayCloseServiceConfig) -> bool:
    try:
        checked = KrDayCloseServiceConfig.model_validate(config.model_dump(mode="python"))
        if checked.expected_commit not in path.name:
            raise InvalidKrDayCloseServiceConfigError
        return publish_private_immutable_text(path, canonical_kr_day_close_service_config(checked))
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayCloseServiceConfigError from None


def load_kr_day_close_service_config(path: Path) -> KrDayCloseServiceConfig:
    try:
        absolute = path.expanduser().absolute()
        payload = read_private_text(absolute)
        config = KrDayCloseServiceConfig.model_validate_json(payload)
        if payload != canonical_kr_day_close_service_config(config):
            raise InvalidKrDayCloseServiceConfigError
        return config
    except (
        InvalidKrDayCloseServiceConfigError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrDayCloseServiceConfigError from None


def require_kr_day_close_service_authority(config: KrDayCloseServiceConfig) -> None:
    head = config.project_root / ".git"
    script = config.project_root / "run_kr_day_close_service.py"
    if (
        not head.exists()
        or not script.is_file()
        or not config.executable_path.is_file()
        or not os.access(config.executable_path, os.X_OK)
    ):
        raise InvalidKrDayCloseServiceConfigError
    completed = subprocess.run(
        ("git", "-C", str(config.project_root), "rev-parse", "HEAD"),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or completed.stdout.strip() != config.expected_commit:
        raise InvalidKrDayCloseServiceConfigError


__all__ = (
    "InvalidKrDayCloseServiceConfigError",
    "KrDayCloseServiceConfig",
    "canonical_kr_day_close_service_config",
    "kr_day_close_service_config_sha256",
    "load_kr_day_close_service_config",
    "require_kr_day_close_service_authority",
    "write_kr_day_close_service_config",
)
