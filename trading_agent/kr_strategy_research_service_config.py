from __future__ import annotations

import hashlib
import json
import os
import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

KR_STRATEGY_RESEARCH_SERVICE_LABEL: Final = "ai.trading-agent.kr-strategy-research-source"
KR_STRATEGY_RESEARCH_SERVICE_INTERVAL_SECONDS: Final = 120
_SERVICE_SCRIPT: Final = "run_kr_strategy_research_service.py"
_CYCLE_SCRIPT: Final = "run_kr_strategy_research_live_cycle.py"


class InvalidKrStrategyResearchServiceError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class KrStrategyResearchServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    label: Literal["ai.trading-agent.kr-strategy-research-source"] = KR_STRATEGY_RESEARCH_SERVICE_LABEL
    project_root: Path
    uv_path: Path
    policy: Path
    database: Path
    experiment_ledger: Path
    delivery_database: Path
    calendar_store: Path
    cycle_root: Path
    live_session_root: Path
    market_context_root: Path
    output_root: Path

    @model_validator(mode="after")
    def require_absolute_paths(self) -> Self:
        paths = tuple(value for name, value in self if name not in {"schema_version", "label"})
        if any(not path.is_absolute() for path in paths):
            raise InvalidKrStrategyResearchServiceError(reason="service_path_not_absolute")
        return self


@dataclass(frozen=True, slots=True)
class KrStrategyResearchLaunchAgentVerification:
    ready: bool
    interval_seconds: int
    config_sha256: str
    plist_sha256: str


def write_kr_strategy_research_service_config(
    path: Path,
    config: KrStrategyResearchServiceConfig,
) -> bool:
    try:
        checked = KrStrategyResearchServiceConfig.model_validate(config.model_dump(mode="python"))
        return publish_private_immutable_text(path, _config_text(checked))
    except (
        InvalidKrStrategyResearchServiceError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrStrategyResearchServiceError(reason="service_config_write_invalid") from None


def load_kr_strategy_research_service_config(path: Path) -> KrStrategyResearchServiceConfig:
    try:
        payload = read_private_text(path.expanduser().absolute())
        config = KrStrategyResearchServiceConfig.model_validate_json(payload)
        if payload != _config_text(config):
            raise InvalidKrStrategyResearchServiceError(reason="service_config_noncanonical")
        return config
    except (
        InvalidKrStrategyResearchServiceError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrStrategyResearchServiceError(reason="service_config_read_invalid") from None


def write_kr_strategy_research_launch_agent(
    path: Path,
    config: KrStrategyResearchServiceConfig,
    config_path: Path,
) -> bool:
    try:
        return publish_private_immutable_text(path, _launch_agent_text(config, config_path))
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValueError):
        raise InvalidKrStrategyResearchServiceError(reason="launch_agent_write_invalid") from None


def verify_kr_strategy_research_launch_agent(
    config_path: Path,
    plist_path: Path,
) -> KrStrategyResearchLaunchAgentVerification:
    try:
        absolute_config = config_path.expanduser().absolute()
        config_text = read_private_text(absolute_config)
        config = load_kr_strategy_research_service_config(absolute_config)
        plist_text = read_private_text(plist_path.expanduser().absolute())
        if plist_text != _launch_agent_text(config, absolute_config):
            raise InvalidKrStrategyResearchServiceError(reason="launch_agent_contract_invalid")
        required = (
            config.uv_path,
            config.project_root / _SERVICE_SCRIPT,
            config.project_root / _CYCLE_SCRIPT,
        )
        if (
            not config.project_root.is_dir()
            or any(not path.is_file() for path in required)
            or not os.access(config.uv_path, os.X_OK)
        ):
            raise InvalidKrStrategyResearchServiceError(reason="service_executable_binding_invalid")
        return KrStrategyResearchLaunchAgentVerification(
            ready=True,
            interval_seconds=KR_STRATEGY_RESEARCH_SERVICE_INTERVAL_SECONDS,
            config_sha256=hashlib.sha256(config_text.encode()).hexdigest(),
            plist_sha256=hashlib.sha256(plist_text.encode()).hexdigest(),
        )
    except (
        InvalidKrStrategyResearchServiceError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrStrategyResearchServiceError(reason="launch_agent_verify_invalid") from None


def _config_text(config: KrStrategyResearchServiceConfig) -> str:
    return (
        json.dumps(
            config.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _launch_agent_text(config: KrStrategyResearchServiceConfig, config_path: Path) -> str:
    payload = {
        "Label": config.label,
        "ProgramArguments": [
            str(config.uv_path),
            "run",
            "--offline",
            "python",
            str(config.project_root / _SERVICE_SCRIPT),
            "tick",
            "--config",
            str(config_path.expanduser().absolute()),
        ],
        "WorkingDirectory": str(config.project_root),
        "RunAtLoad": True,
        "StartInterval": KR_STRATEGY_RESEARCH_SERVICE_INTERVAL_SECONDS,
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Umask": 0o077,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


__all__ = (
    "KR_STRATEGY_RESEARCH_SERVICE_INTERVAL_SECONDS",
    "KR_STRATEGY_RESEARCH_SERVICE_LABEL",
    "InvalidKrStrategyResearchServiceError",
    "KrStrategyResearchServiceConfig",
    "load_kr_strategy_research_service_config",
    "verify_kr_strategy_research_launch_agent",
    "write_kr_strategy_research_launch_agent",
    "write_kr_strategy_research_service_config",
)
