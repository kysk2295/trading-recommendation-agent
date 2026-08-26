from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from trading_agent.local_browser_gateway_config import (
    InvalidLocalBrowserGatewayConfigError,
    LocalBrowserGatewayConfig,
    load_local_browser_gateway_config,
    verify_local_browser_launch_agent,
    write_local_browser_gateway_config,
    write_local_browser_launch_agent,
)

CONFIG_READ_ERROR = "local_browser_gateway_config_read_invalid"
LAUNCH_VERIFY_ERROR = "local_browser_launch_agent_verify_invalid"
SYMLINK_COMPONENT_ERROR = "local_browser_gateway_symlink_component_invalid"


@dataclass(frozen=True, slots=True)
class GatewayFixture:
    config: LocalBrowserGatewayConfig
    config_path: Path
    plist_path: Path


def build_gateway_fixture(tmp_path: Path) -> GatewayFixture:
    project_root = tmp_path / "project"
    project_root.mkdir()
    gateway_script = project_root / "run_local_browser_gateway.py"
    gateway_script.write_text("pass\n", encoding="utf-8")
    binaries = tmp_path / "binaries"
    binaries.mkdir()
    uv_path = binaries / "uv"
    chrome_executable = binaries / "chrome"
    for executable in (uv_path, chrome_executable):
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    config = LocalBrowserGatewayConfig(
        project_root=project_root,
        uv_path=uv_path,
        chrome_executable=chrome_executable,
        state_root=tmp_path / "runtime" / "state",
        profile_root=tmp_path / "runtime" / "profile",
        socket_path=tmp_path / "runtime" / "state" / "gateway.sock",
        receipt_database=tmp_path / "runtime" / "state" / "receipts.sqlite3",
        screenshot_root=tmp_path / "runtime" / "state" / "screenshots",
    )
    return GatewayFixture(config, private / "gateway.json", private / "gateway.plist")


def config_from(config: LocalBrowserGatewayConfig, **changes: Path) -> LocalBrowserGatewayConfig:
    payload = config.model_dump(mode="python")
    payload.update(changes)
    return LocalBrowserGatewayConfig.model_validate(payload)


def write_contract(fixture: GatewayFixture) -> None:
    assert write_local_browser_gateway_config(fixture.config_path, fixture.config)
    assert write_local_browser_launch_agent(fixture.plist_path, fixture.config, fixture.config_path)


def rejection[T](action: Callable[[], T]) -> InvalidLocalBrowserGatewayConfigError:
    with pytest.raises(InvalidLocalBrowserGatewayConfigError) as raised:
        _ = action()
    return raised.value


def load_rejection(fixture: GatewayFixture) -> InvalidLocalBrowserGatewayConfigError:
    return rejection(lambda: load_local_browser_gateway_config(fixture.config_path))


def verify_rejection(fixture: GatewayFixture) -> InvalidLocalBrowserGatewayConfigError:
    return rejection(lambda: verify_local_browser_launch_agent(fixture.config_path, fixture.plist_path))


def replace_with_symlink(path: Path) -> None:
    target = path.with_name(f"{path.name}.target")
    target.write_text("fixture\n", encoding="utf-8")
    target.chmod(0o600)
    path.unlink()
    path.symlink_to(target)
