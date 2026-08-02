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
    read_private_text,
)
from trading_agent.research_agent_service_config import (
    RESEARCH_AGENT_SERVICE_LABEL,
    InvalidResearchAgentServiceConfigError,
    load_research_agent_service_config,
    verify_research_agent_launch_agent,
)
from trading_agent.research_agent_sources import ResearchAgentSourcePaths

_SERVICE_SCRIPT: Final = "run_research_agent_runtime.py"
_SYSTEMATIC_SCRIPT: Final = "run_autonomous_research_cycle.py"


class LegacySystematicResearchActionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    project_root: Path
    uv_executable: Path
    python_executable: Path
    context: Path
    response_fixture: Path | None
    hermes_executable: Path | None
    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    provider_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    experiment_ledger: Path
    receipt_root: Path
    strategy_root: Path
    manifest_root: Path
    queue_root: Path
    input_csv: Path
    data_foundation_manifest: Path
    artifact_root: Path
    review_root: Path
    runs_root: Path
    max_runtime_seconds: float = Field(gt=0, le=3_600)
    max_bars: int = Field(default=100_000, ge=1, le=100_000)
    max_sessions: int = Field(default=60, ge=1, le=60)
    rss_limit_gib: float = Field(default=9.5, gt=0, le=10.0)

    @model_validator(mode="after")
    def require_absolute_provider_binding(self) -> Self:
        paths = (
            self.project_root,
            self.uv_executable,
            self.python_executable,
            self.context,
            self.experiment_ledger,
            self.receipt_root,
            self.strategy_root,
            self.manifest_root,
            self.queue_root,
            self.input_csv,
            self.data_foundation_manifest,
            self.artifact_root,
            self.review_root,
            self.runs_root,
        )
        if any(not path.is_absolute() for path in paths):
            raise InvalidResearchAgentServiceConfigError(reason="legacy_systematic_path_not_absolute")
        if self.response_fixture is not None and not self.response_fixture.is_absolute():
            raise InvalidResearchAgentServiceConfigError(reason="legacy_systematic_path_not_absolute")
        if self.hermes_executable is not None and not self.hermes_executable.is_absolute():
            raise InvalidResearchAgentServiceConfigError(reason="legacy_systematic_path_not_absolute")
        if (self.response_fixture is None) == (self.hermes_executable is None):
            raise InvalidResearchAgentServiceConfigError(reason="legacy_systematic_provider_binding_invalid")
        return self


class LegacyResearchAgentServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1]
    label: Literal["ai.trading-agent.research-agent-runtime"]
    project_root: Path
    uv_path: Path
    hermes_executable: Path
    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    provider_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    cycle_database: Path
    output_root: Path
    hermes_database: Path
    source_paths: ResearchAgentSourcePaths
    systematic: LegacySystematicResearchActionConfig

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
            raise InvalidResearchAgentServiceConfigError(reason="legacy_service_path_not_absolute")
        if self.systematic.project_root != self.project_root or self.systematic.uv_executable != self.uv_path:
            raise InvalidResearchAgentServiceConfigError(reason="legacy_systematic_service_binding_invalid")
        if (
            self.systematic.hermes_executable is not None
            and self.systematic.hermes_executable != self.hermes_executable
        ):
            raise InvalidResearchAgentServiceConfigError(reason="legacy_systematic_hermes_binding_invalid")
        if self.systematic.provider_id != self.provider_id:
            raise InvalidResearchAgentServiceConfigError(reason="legacy_systematic_provider_binding_invalid")
        return self


@dataclass(frozen=True, slots=True)
class ResearchAgentReplaceCurrentVerification:
    project_root: Path
    config_sha256: str
    plist_sha256: str


def verify_research_agent_replace_current(
    config_path: Path,
    plist_path: Path,
) -> ResearchAgentReplaceCurrentVerification:
    try:
        return _verify_v2_or_legacy_current(config_path, plist_path)
    except (
        InvalidPrivateImmutableFileError,
        InvalidResearchAgentServiceConfigError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidResearchAgentServiceConfigError(reason="replace_current_verify_invalid") from None


def _verify_v2_or_legacy_current(
    config_path: Path,
    plist_path: Path,
) -> ResearchAgentReplaceCurrentVerification:
    try:
        current = load_research_agent_service_config(config_path)
    except InvalidResearchAgentServiceConfigError:
        return _verify_legacy_current(config_path, plist_path)
    verified = verify_research_agent_launch_agent(config_path, plist_path)
    return ResearchAgentReplaceCurrentVerification(
        project_root=current.project_root,
        config_sha256=verified.config_sha256,
        plist_sha256=verified.plist_sha256,
    )


def _verify_legacy_current(
    config_path: Path,
    plist_path: Path,
) -> ResearchAgentReplaceCurrentVerification:
    absolute_config = config_path.expanduser().absolute()
    config_payload = read_private_text(absolute_config)
    config = LegacyResearchAgentServiceConfig.model_validate_json(config_payload)
    if config_payload != _legacy_config_text(config):
        raise InvalidResearchAgentServiceConfigError(reason="legacy_service_config_noncanonical")
    plist_payload = read_private_text(plist_path.expanduser().absolute())
    if plist_payload != _legacy_launch_agent_text(config, absolute_config):
        raise InvalidResearchAgentServiceConfigError(reason="legacy_launch_agent_contract_invalid")
    required_files = (
        config.uv_path,
        config.hermes_executable,
        config.systematic.python_executable,
        config.project_root / _SERVICE_SCRIPT,
        config.project_root / _SYSTEMATIC_SCRIPT,
    )
    executables = (config.uv_path, config.hermes_executable, config.systematic.python_executable)
    if (
        not config.project_root.is_dir()
        or any(not path.is_file() for path in required_files)
        or any(not os.access(path, os.X_OK) for path in executables)
    ):
        raise InvalidResearchAgentServiceConfigError(reason="legacy_service_executable_binding_invalid")
    return ResearchAgentReplaceCurrentVerification(
        project_root=config.project_root,
        config_sha256=hashlib.sha256(config_payload.encode()).hexdigest(),
        plist_sha256=hashlib.sha256(plist_payload.encode()).hexdigest(),
    )


def _legacy_config_text(config: LegacyResearchAgentServiceConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


def _legacy_launch_agent_text(config: LegacyResearchAgentServiceConfig, config_path: Path) -> str:
    payload = {
        "KeepAlive": True,
        "Label": RESEARCH_AGENT_SERVICE_LABEL,
        "ProcessType": "Background",
        "ProgramArguments": [
            str(config.uv_path),
            "run",
            "--offline",
            "python",
            str(config.project_root / _SERVICE_SCRIPT),
            "run",
            "--config",
            str(config_path),
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
    "ResearchAgentReplaceCurrentVerification",
    "verify_research_agent_replace_current",
)
