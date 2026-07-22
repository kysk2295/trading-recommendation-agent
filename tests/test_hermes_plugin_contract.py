from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path


class FakePluginContext:
    def __init__(self) -> None:
        self.tools: dict[str, dict] = {}
        self.commands: dict[str, Callable[..., str]] = {}
        self.skills: dict[str, Path] = {}

    def register_tool(self, **definition) -> None:
        self.tools[definition["name"]] = definition

    def register_command(self, name, handler, **metadata) -> None:
        self.commands[name] = handler

    def register_skill(self, name, path, description="") -> None:
        self.skills[name] = path


def test_plugin_registers_query_arm_tools_command_and_skill() -> None:
    # Given
    plugin = _load_plugin()
    context = FakePluginContext()

    # When
    plugin.register(context)

    # Then
    assert set(context.tools) == {
        "trading_agent_query",
        "trading_agent_status",
        "trading_agent_arm_prepare",
        "trading_agent_arm_confirm",
        "trading_agent_arm_revoke",
    }
    assert set(context.commands) == {"trading-status"}
    assert set(context.skills) == {"trading-agent"}
    assert all(definition["toolset"] == "trading_agent" for definition in context.tools.values())
    assert all(
        "broker_url" not in definition["schema"]["parameters"]["properties"] for definition in context.tools.values()
    )
    assert all(
        "credential" not in definition["schema"]["parameters"]["properties"] for definition in context.tools.values()
    )


def test_query_handler_executes_only_allowlisted_project_cli(tmp_path: Path, monkeypatch) -> None:
    # Given
    plugin = _load_plugin()
    context = FakePluginContext()
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (project / "run_hermes_delivery.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setenv("TRADING_AGENT_PROJECT_ROOT", str(project))
    calls: list[tuple[str, ...]] = []

    class Completed:
        returncode = 0
        stdout = '{"result":"queried"}\n'

    def fake_run(command, **kwargs):
        calls.append(tuple(command))
        return Completed()

    monkeypatch.setattr(plugin.subprocess, "run", fake_run)
    plugin.register(context)
    handler = context.tools["trading_agent_query"]["handler"]

    # When
    result = json.loads(handler({"symbol": "ACME", "observed_at": "2026-07-22T14:00:00+00:00"}))
    blocked = json.loads(handler({"symbol": "ACME", "broker_url": "https://example.invalid"}))

    # Then
    assert result == {"result": "queried"}
    assert blocked == {"reason": "invalid_arguments", "result": "blocked"}
    assert len(calls) == 1
    command = calls[0]
    assert command[1:4] == ("run", "python", str(project / "run_hermes_delivery.py"))
    assert "query" in command
    assert "https://example.invalid" not in command


def test_arm_handler_derives_owner_from_session_context_and_fails_closed(tmp_path: Path, monkeypatch) -> None:
    # Given
    plugin = _load_plugin()
    context = FakePluginContext()
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (project / "run_hermes_delivery.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setenv("TRADING_AGENT_PROJECT_ROOT", str(project))
    plugin.register(context)
    handler = context.tools["trading_agent_arm_prepare"]["handler"]

    # When
    missing_context = json.loads(handler({"session_id": "XNYS-2026-07-22", "lane_id": "intraday_momentum"}))
    unavailable = json.loads(
        handler(
            {"session_id": "XNYS-2026-07-22", "lane_id": "intraday_momentum"},
            platform="telegram",
            sender_id="owner-1",
            session_id="chat-session-1",
        )
    )

    # Then
    assert missing_context == {"reason": "owner_context_missing", "result": "blocked"}
    assert unavailable == {"reason": "arm_gateway_unavailable", "result": "blocked"}


def _load_plugin():
    root = Path(__file__).parents[1]
    source = root / "integrations" / "hermes" / "trading-agent" / "__init__.py"
    specification = importlib.util.spec_from_file_location("trading_agent_hermes_plugin", source)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module
