from __future__ import annotations

import hashlib
import json
import os
import plistlib
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

LOCAL_BROWSER_GATEWAY_LABEL: Final = "ai.trading-agent.local-browser-gateway"
_GATEWAY_SCRIPT: Final = "run_local_browser_gateway.py"


class InvalidLocalBrowserGatewayConfigError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class LocalBrowserGatewayConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    label: Literal["ai.trading-agent.local-browser-gateway"] = LOCAL_BROWSER_GATEWAY_LABEL
    project_root: Path
    uv_path: Path
    chrome_executable: Path
    state_root: Path
    profile_root: Path
    socket_path: Path
    receipt_database: Path
    screenshot_root: Path
    startup_timeout_seconds: float = Field(default=30.0, ge=1.0, le=60.0)
    command_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)

    @model_validator(mode="after")
    def require_isolated_absolute_paths(self) -> Self:
        paths = (
            self.project_root,
            self.uv_path,
            self.chrome_executable,
            self.state_root,
            self.profile_root,
            self.socket_path,
            self.receipt_database,
            self.screenshot_root,
        )
        if any(not path.is_absolute() for path in paths):
            raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_gateway_path_not_absolute")
        if any(_has_existing_symlink_component(path) for path in paths):
            raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_gateway_symlink_component_invalid")
        project_root, state_root, profile_root = (
            self.project_root.resolve(strict=False),
            self.state_root.resolve(strict=False),
            self.profile_root.resolve(strict=False),
        )
        if state_root.is_relative_to(project_root) or profile_root.is_relative_to(project_root):
            raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_gateway_root_inside_project")
        if state_root.is_relative_to(profile_root) or profile_root.is_relative_to(state_root):
            raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_gateway_roots_overlap")
        if any(
            not _is_descendant(path.resolve(strict=False), state_root)
            for path in (self.socket_path, self.receipt_database, self.screenshot_root)
        ):
            raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_gateway_state_descendant_invalid")
        return self


@dataclass(frozen=True, slots=True)
class LocalBrowserLaunchAgentVerification:
    ready: bool
    config_sha256: str
    plist_sha256: str


def write_local_browser_gateway_config(path: Path, config: LocalBrowserGatewayConfig) -> bool:
    try:
        private_path = _absolute_private_path(path)
        checked = LocalBrowserGatewayConfig.model_validate(config.model_dump(mode="python"))
        reparsed = LocalBrowserGatewayConfig.model_validate_json(_config_text(checked))
        return publish_private_immutable_text(private_path, _config_text(reparsed))
    except (
        InvalidPrivateImmutableFileError,
        InvalidLocalBrowserGatewayConfigError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_gateway_config_write_invalid") from None


def load_local_browser_gateway_config(path: Path) -> LocalBrowserGatewayConfig:
    try:
        private_path = _absolute_private_path(path)
        payload = read_private_text(private_path)
        config = LocalBrowserGatewayConfig.model_validate_json(payload)
        if payload != _config_text(config):
            raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_gateway_config_noncanonical")
        return config
    except (
        InvalidPrivateImmutableFileError,
        InvalidLocalBrowserGatewayConfigError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_gateway_config_read_invalid") from None


def canonical_local_browser_gateway_config_sha256(config: LocalBrowserGatewayConfig) -> str:
    return hashlib.sha256(_config_text(config).encode("utf-8")).hexdigest()


def write_local_browser_launch_agent(path: Path, config: LocalBrowserGatewayConfig, config_path: Path) -> bool:
    try:
        private_path = _absolute_private_path(path)
        private_config_path = _absolute_private_path(config_path)
        checked = LocalBrowserGatewayConfig.model_validate(config.model_dump(mode="python"))
        return publish_private_immutable_text(private_path, _launch_agent_text(checked, private_config_path))
    except (
        InvalidPrivateImmutableFileError,
        InvalidLocalBrowserGatewayConfigError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_launch_agent_write_invalid") from None


def verify_local_browser_launch_agent(config_path: Path, plist_path: Path) -> LocalBrowserLaunchAgentVerification:
    try:
        private_config_path = _absolute_private_path(config_path)
        private_plist_path = _absolute_private_path(plist_path)
        config = load_local_browser_gateway_config(private_config_path)
        plist_payload = read_private_text(private_plist_path)
        if plist_payload != _launch_agent_text(config, private_config_path):
            raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_launch_agent_contract_invalid")
        required_files = (
            config.uv_path,
            config.chrome_executable,
            config.project_root / _GATEWAY_SCRIPT,
        )
        if not config.project_root.is_dir() or any(not _is_regular_file(path) for path in required_files):
            raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_launch_agent_binding_invalid")
        if any(not os.access(path, os.X_OK) for path in (config.uv_path, config.chrome_executable)):
            raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_launch_agent_binding_invalid")
        return LocalBrowserLaunchAgentVerification(
            ready=True,
            config_sha256=canonical_local_browser_gateway_config_sha256(config),
            plist_sha256=hashlib.sha256(plist_payload.encode("utf-8")).hexdigest(),
        )
    except (
        InvalidPrivateImmutableFileError,
        InvalidLocalBrowserGatewayConfigError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_launch_agent_verify_invalid") from None


def _is_descendant(path: Path, root: Path) -> bool:
    return path != root and path.is_relative_to(root)


def _is_regular_file(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
        return not stat.S_ISLNK(mode) and stat.S_ISREG(mode)
    except OSError:
        return False


def _has_existing_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if stat.S_ISLNK(mode):
            return True
    return False


def _absolute_private_path(path: Path) -> Path:
    if not path.is_absolute():
        raise InvalidLocalBrowserGatewayConfigError(reason="local_browser_gateway_private_path_not_absolute")
    return path.expanduser().absolute()


def _config_text(config: LocalBrowserGatewayConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"


def _launch_agent_text(config: LocalBrowserGatewayConfig, config_path: Path) -> str:
    payload = {
        "KeepAlive": True,
        "Label": config.label,
        "ProcessType": "Background",
        "ProgramArguments": [
            str(config.uv_path),
            "run",
            "--offline",
            "python",
            str(config.project_root / _GATEWAY_SCRIPT),
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
    "LOCAL_BROWSER_GATEWAY_LABEL",
    "InvalidLocalBrowserGatewayConfigError",
    "LocalBrowserGatewayConfig",
    "LocalBrowserLaunchAgentVerification",
    "canonical_local_browser_gateway_config_sha256",
    "load_local_browser_gateway_config",
    "verify_local_browser_launch_agent",
    "write_local_browser_gateway_config",
    "write_local_browser_launch_agent",
)
