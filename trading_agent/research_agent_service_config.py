from __future__ import annotations

import hashlib
import json
import os
import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.research_agent_sources import ResearchAgentSourcePaths
from trading_agent.research_agent_systematic import SystematicResearchActionConfig

RESEARCH_AGENT_SERVICE_LABEL: Final = "ai.trading-agent.research-agent-runtime"
_SERVICE_SCRIPT: Final = "run_research_agent_runtime.py"
_SYSTEMATIC_SCRIPT: Final = "run_autonomous_research_cycle.py"


class InvalidResearchAgentServiceConfigError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ResearchAgentServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    label: Literal["ai.trading-agent.research-agent-runtime"] = RESEARCH_AGENT_SERVICE_LABEL
    project_root: Path
    uv_path: Path
    hermes_executable: Path
    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    cycle_database: Path
    output_root: Path
    hermes_database: Path
    source_paths: ResearchAgentSourcePaths
    systematic: SystematicResearchActionConfig

    @model_validator(mode="after")
    def require_absolute_bound_configuration(self) -> Self:
        direct = (
            self.project_root,
            self.uv_path,
            self.hermes_executable,
            self.cycle_database,
            self.output_root,
            self.hermes_database,
        )
        if any(not path.is_absolute() for path in direct):
            raise InvalidResearchAgentServiceConfigError(reason="service_path_not_absolute")
        if self.systematic.project_root != self.project_root or self.systematic.uv_executable != self.uv_path:
            raise InvalidResearchAgentServiceConfigError(reason="systematic_service_binding_invalid")
        if (
            self.systematic.hermes_executable is not None
            and self.systematic.hermes_executable != self.hermes_executable
        ):
            raise InvalidResearchAgentServiceConfigError(reason="systematic_hermes_binding_invalid")
        return self


@dataclass(frozen=True, slots=True)
class ResearchAgentLaunchAgentVerification:
    ready: bool
    config_sha256: str
    plist_sha256: str


def write_research_agent_service_config(path: Path, config: ResearchAgentServiceConfig) -> bool:
    try:
        checked = ResearchAgentServiceConfig.model_validate(config.model_dump(mode="python"))
        return publish_private_immutable_text(path, _config_text(checked))
    except (
        InvalidPrivateImmutableFileError,
        InvalidResearchAgentServiceConfigError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidResearchAgentServiceConfigError(reason="service_config_write_invalid") from None


def load_research_agent_service_config(path: Path) -> ResearchAgentServiceConfig:
    try:
        payload = read_private_text(path.expanduser().absolute())
        config = ResearchAgentServiceConfig.model_validate_json(payload)
        if payload != _config_text(config):
            raise InvalidResearchAgentServiceConfigError(reason="service_config_noncanonical")
        return config
    except (
        InvalidPrivateImmutableFileError,
        InvalidResearchAgentServiceConfigError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidResearchAgentServiceConfigError(reason="service_config_read_invalid") from None


def write_research_agent_launch_agent(
    path: Path,
    config: ResearchAgentServiceConfig,
    config_path: Path,
) -> bool:
    try:
        return publish_private_immutable_text(path, _launch_agent_text(config, config_path))
    except (
        InvalidPrivateImmutableFileError,
        InvalidResearchAgentServiceConfigError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise InvalidResearchAgentServiceConfigError(reason="launch_agent_write_invalid") from None


def verify_research_agent_launch_agent(
    config_path: Path,
    plist_path: Path,
) -> ResearchAgentLaunchAgentVerification:
    try:
        absolute_config = config_path.expanduser().absolute()
        config_payload = read_private_text(absolute_config)
        config = load_research_agent_service_config(absolute_config)
        plist_payload = read_private_text(plist_path.expanduser().absolute())
        if plist_payload != _launch_agent_text(config, absolute_config):
            raise InvalidResearchAgentServiceConfigError(reason="launch_agent_contract_invalid")
        required_files = (
            config.uv_path,
            config.hermes_executable,
            config.systematic.python_executable,
            config.project_root / _SERVICE_SCRIPT,
            config.project_root / _SYSTEMATIC_SCRIPT,
        )
        if (
            not config.project_root.is_dir()
            or any(not path.is_file() for path in required_files)
            or any(not os.access(path, os.X_OK) for path in (config.uv_path, config.hermes_executable))
        ):
            raise InvalidResearchAgentServiceConfigError(reason="service_executable_binding_invalid")
        return ResearchAgentLaunchAgentVerification(
            ready=True,
            config_sha256=hashlib.sha256(config_payload.encode()).hexdigest(),
            plist_sha256=hashlib.sha256(plist_payload.encode()).hexdigest(),
        )
    except (
        InvalidPrivateImmutableFileError,
        InvalidResearchAgentServiceConfigError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidResearchAgentServiceConfigError(reason="launch_agent_verify_invalid") from None


def _config_text(config: ResearchAgentServiceConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


def _launch_agent_text(config: ResearchAgentServiceConfig, config_path: Path) -> str:
    payload = {
        "KeepAlive": True,
        "Label": config.label,
        "ProcessType": "Background",
        "ProgramArguments": [
            str(config.uv_path),
            "run",
            "--offline",
            "python",
            str(config.project_root / _SERVICE_SCRIPT),
            "run",
            "--config",
            str(config_path.expanduser().absolute()),
        ],
        "RunAtLoad": True,
        "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null",
        "ThrottleInterval": 30,
        "Umask": 0o077,
        "WorkingDirectory": str(config.project_root),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


__all__ = (
    "RESEARCH_AGENT_SERVICE_LABEL",
    "InvalidResearchAgentServiceConfigError",
    "ResearchAgentLaunchAgentVerification",
    "ResearchAgentServiceConfig",
    "load_research_agent_service_config",
    "verify_research_agent_launch_agent",
    "write_research_agent_launch_agent",
    "write_research_agent_service_config",
)
