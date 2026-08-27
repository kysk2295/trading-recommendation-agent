from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)


class InvalidKrLoopAutomationConfigError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop automation config is invalid"


class KrLoopAutomationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    repository: Path
    output_root: Path
    research_agent_config: Path
    active_release: Path
    launch_agents_directory: Path
    uv_path: Path
    grok_binary: Path
    paper_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def require_absolute_paths(self) -> Self:
        paths = (
            self.repository,
            self.output_root,
            self.research_agent_config,
            self.active_release,
            self.launch_agents_directory,
            self.uv_path,
            self.grok_binary,
        )
        if any(not path.is_absolute() for path in paths):
            raise InvalidKrLoopAutomationConfigError
        return self

    @property
    def loop_root(self) -> Path:
        return self.output_root / "autonomous-supervisor" / "kr-v1"

    @property
    def loop_database(self) -> Path:
        return self.loop_root / "kr-loop-engineer.sqlite3"

    @property
    def artifact_root(self) -> Path:
        return self.loop_root / "loop-artifacts"

    @property
    def task_root(self) -> Path:
        return self.loop_root / "loop-tasks"

    @property
    def shadow_root(self) -> Path:
        return self.loop_root / "loop-shadow"


def write_kr_loop_automation_config(path: Path, config: KrLoopAutomationConfig) -> bool:
    try:
        trusted = KrLoopAutomationConfig.model_validate(config.model_dump(mode="python"))
        return publish_private_immutable_text(path, canonical_kr_loop_automation_config(trusted))
    except (
        InvalidKrLoopAutomationConfigError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrLoopAutomationConfigError from None


def load_kr_loop_automation_config(path: Path) -> KrLoopAutomationConfig:
    try:
        payload = read_private_text(path.expanduser().absolute())
        config = KrLoopAutomationConfig.model_validate_json(payload)
        if payload != canonical_kr_loop_automation_config(config):
            raise InvalidKrLoopAutomationConfigError
        return config
    except (
        InvalidKrLoopAutomationConfigError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrLoopAutomationConfigError from None


def canonical_kr_loop_automation_config(config: KrLoopAutomationConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


def kr_loop_automation_config_sha256(config: KrLoopAutomationConfig) -> str:
    return hashlib.sha256(canonical_kr_loop_automation_config(config).encode()).hexdigest()


__all__ = (
    "InvalidKrLoopAutomationConfigError",
    "KrLoopAutomationConfig",
    "canonical_kr_loop_automation_config",
    "kr_loop_automation_config_sha256",
    "load_kr_loop_automation_config",
    "write_kr_loop_automation_config",
)
