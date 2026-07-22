from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


class PluginContextStub:
    def register_tool(self, **definition) -> None:
        _ = definition

    def register_command(self, name, handler, **metadata) -> None:
        _ = (name, handler, metadata)

    def register_skill(self, name, path, description="") -> None:
        _ = (name, path, description)


def test_plugin_registration_keeps_delivery_disabled_without_owner_opt_in(monkeypatch) -> None:
    # Given
    monkeypatch.delenv("TRADING_AGENT_HERMES_DELIVERY_ENABLED", raising=False)
    plugin = _plugin_module()
    starts: list[Path] = []
    monkeypatch.setattr(plugin, "_start_delivery_worker", lambda: starts.append(Path("unexpected")))

    # When
    plugin.register(PluginContextStub())

    # Then
    assert starts == []


def test_plugin_registration_starts_delivery_after_owner_opt_in(monkeypatch) -> None:
    # Given
    monkeypatch.setenv("TRADING_AGENT_HERMES_DELIVERY_ENABLED", "1")
    plugin = _plugin_module()
    starts: list[bool] = []
    monkeypatch.setattr(plugin, "_start_delivery_worker", lambda: starts.append(True))

    # When
    plugin.register(PluginContextStub())

    # Then
    assert starts == [True]


def test_plugin_status_reports_disabled_at_least_once_contract(tmp_path: Path, monkeypatch) -> None:
    # Given
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (project / "run_hermes_delivery.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setenv("TRADING_AGENT_PROJECT_ROOT", str(project))
    plugin = _plugin_module()
    monkeypatch.setattr(plugin._DELIVERY_STATE, "status", plugin._PluginDeliveryStatus.DISABLED)

    # When
    status = json.loads(plugin._status({}))

    # Then
    assert status == {
        "arm_gateway_available": False,
        "delivery_database_available": False,
        "delivery_semantics": "at_least_once",
        "delivery_worker_status": "disabled",
        "query_available": True,
        "result": "ready",
        "version": "1.1.0",
    }


def test_telegram_sender_keeps_hermes_credentials_out_of_repr(monkeypatch) -> None:
    # Given
    plugin = _plugin_module()
    sender_module = __import__(f"{plugin.__name__}.telegram_sender", fromlist=["HermesTelegramSender"])
    loaded: list[bool] = []
    gateway = SimpleNamespace(
        Platform=SimpleNamespace(TELEGRAM="telegram"),
        load_gateway_config=lambda: SimpleNamespace(
            platforms={"telegram": SimpleNamespace(token="dummy-secret-token")},
            get_home_channel=lambda platform: SimpleNamespace(
                chat_id="123456789",
                thread_id=None,
                platform=platform,
            ),
        ),
    )
    send_command = SimpleNamespace(_load_hermes_env=lambda: loaded.append(True))
    originals = {"gateway.config": gateway, "hermes_cli.send_cmd": send_command}
    monkeypatch.setattr(sender_module.importlib, "import_module", lambda name: originals[name])

    # When
    sender = sender_module.HermesTelegramSender.from_hermes_config()
    representation = repr(sender)

    # Then
    assert loaded == [True]
    assert "dummy-secret-token" not in representation
    assert "123456789" not in representation


def _plugin_module():
    name = "trading_agent_hermes_plugin"
    if name in sys.modules:
        return sys.modules[name]
    root = Path(__file__).parents[1] / "integrations" / "hermes" / "trading-agent"
    specification = importlib.util.spec_from_file_location(
        name,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module
