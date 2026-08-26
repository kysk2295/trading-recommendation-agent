from __future__ import annotations

from pathlib import Path

from tests.test_research_agent_service_cli import _config
from trading_agent.local_browser_gateway_config import (
    LocalBrowserGatewayConfig,
    write_local_browser_gateway_config,
)
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig


def browser_gateway_config(tmp_path: Path) -> tuple[LocalBrowserGatewayConfig, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    project_root = tmp_path / "gateway-project"
    project_root.mkdir()
    binaries = tmp_path / "gateway-binaries"
    binaries.mkdir()
    uv_path = binaries / "uv"
    chrome_path = binaries / "chrome"
    for executable in (uv_path, chrome_path):
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
    state_root = tmp_path / "gateway-state"
    config = LocalBrowserGatewayConfig(
        project_root=project_root,
        uv_path=uv_path,
        chrome_executable=chrome_path,
        state_root=state_root,
        profile_root=tmp_path / "gateway-profile",
        socket_path=state_root / "browser.sock",
        receipt_database=state_root / "receipts.sqlite3",
        screenshot_root=state_root / "screenshots",
    )
    config_path = tmp_path / "gateway-private" / "gateway.json"
    assert write_local_browser_gateway_config(config_path, config)
    return config, config_path


def browser_service_config(tmp_path: Path) -> ResearchAgentServiceConfig:
    _, gateway_path = browser_gateway_config(tmp_path)
    config = _config(tmp_path)
    return ResearchAgentServiceConfig.model_validate(
        config.model_dump(mode="python") | {"schema_version": 3, "browser_gateway_config": gateway_path.absolute()}
    )
