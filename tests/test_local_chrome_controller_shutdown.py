from __future__ import annotations

from pathlib import Path

import pytest

import trading_agent.local_browser_private_fs as private_fs
import trading_agent.local_chrome_controller as chrome
from tests.test_local_chrome_controller import FakeLauncher, FakeProbe, FakeProcess, _controller, _payload, build_config
from trading_agent.local_browser_gateway_config import LocalBrowserGatewayConfig


@pytest.fixture
def config(tmp_path: Path) -> LocalBrowserGatewayConfig:
    return build_config(tmp_path)


def test_close_leaves_healthy_attached_endpoint(config: LocalBrowserGatewayConfig) -> None:
    # Given: a valid endpoint belonging to another controller.
    config.state_root.mkdir(parents=True, mode=0o700)
    config.profile_root.mkdir(mode=0o700)
    (config.profile_root / "DevToolsActivePort").write_bytes(_payload())
    (config.profile_root / "DevToolsActivePort").chmod(0o600)
    launcher = FakeLauncher(config.profile_root, [], [])
    local_controller = _controller(config, launcher, FakeProbe({(9222, "/devtools/browser/token")}))
    # When: it is made ready then closed.
    endpoint = local_controller.ensure_ready()
    local_controller.close()
    # Then: it reports honest attachment and never starts or kills Chrome.
    assert endpoint.ownership == "attached" and endpoint.process_id is None
    assert launcher.commands == []


def test_malformed_owned_port_file_reaps_owned_process_without_replacement(config: LocalBrowserGatewayConfig) -> None:
    # Given: an owned healthy Chrome whose port file becomes malformed.
    process = FakeProcess(101)
    launcher = FakeLauncher(config.profile_root, [process], [_payload()])
    local_controller = _controller(config, launcher, FakeProbe({(9222, "/devtools/browser/token")}))
    assert local_controller.ensure_ready().process_id == 101
    port_file = config.profile_root / "DevToolsActivePort"
    port_file.write_bytes(_payload().replace(b"\n", b"\r\n"))
    port_file.chmod(0o600)
    # When: the owned endpoint file can no longer be parsed.
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = local_controller.ensure_ready()
    # Then: only the owned process is reaped and its exact stale file is removed.
    assert raised.value.reason == "local_chrome_port_file_invalid"
    assert (process.terminated, process.waits, len(launcher.commands), port_file.exists()) == (1, 1, 1, False)


def test_owned_cleanup_preserves_replaced_port_file(
    config: LocalBrowserGatewayConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an owned endpoint whose name is replaced immediately before cleanup.
    process = FakeProcess(101)
    launcher = FakeLauncher(config.profile_root, [process], [_payload()])
    probe = FakeProbe({(9222, "/devtools/browser/token")})
    local_controller = _controller(config, launcher, probe)
    assert local_controller.ensure_ready().process_id == 101
    replacement = _payload(9223)
    original = chrome.unlink_private_browser_file

    def replace_file(
        directory: private_fs.PrivateBrowserDirectory,
        name: str,
        expected: private_fs.PrivateBrowserFile,
        owner_id: int,
    ) -> None:
        path = config.profile_root / name
        path.unlink()
        path.write_bytes(replacement)
        path.chmod(0o600)
        original(directory, name, expected, owner_id)

    probe.healthy.clear()
    monkeypatch.setattr(chrome, "unlink_private_browser_file", replace_file)
    # When: unhealthy owned cleanup reaches the exact-inode unlink boundary.
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = local_controller.ensure_ready()
    # Then: the replacement remains and no second Chrome starts.
    assert raised.value.reason == "local_chrome_port_file_invalid"
    assert (
        process.terminated,
        process.waits,
        len(launcher.commands),
        (config.profile_root / "DevToolsActivePort").read_bytes(),
    ) == (1, 1, 1, replacement)


def test_close_terminates_then_kills_only_owned_process_and_is_idempotent(config: LocalBrowserGatewayConfig) -> None:
    # Given: a ready owned process whose first wait times out.
    process = FakeProcess(101, timeout_waits=1)
    launcher = FakeLauncher(config.profile_root, [process], [_payload()])
    local_controller = _controller(config, launcher, FakeProbe({(9222, "/devtools/browser/token")}))
    assert local_controller.ensure_ready().process_id == 101
    # When: close is called twice.
    local_controller.close()
    local_controller.close()
    # Then: termination escalates only for the process this controller launched.
    assert (process.terminated, process.killed, process.waits) == (1, 1, 2)


def test_close_guards_owned_snapshot_and_ready_restarts(config: LocalBrowserGatewayConfig) -> None:
    # Given: a ready owned endpoint, followed by a distinct launchable replacement.
    first, restarted = FakeProcess(101), FakeProcess(202)
    launcher = FakeLauncher(config.profile_root, [first, restarted], [_payload(), _payload(9223)])
    probe = FakeProbe({(9222, "/devtools/browser/token"), (9223, "/devtools/browser/token")})
    local_controller = _controller(config, launcher, probe)
    assert local_controller.ensure_ready().process_id == 101
    # When: close removes its observed port file and readiness is requested again.
    local_controller.close()
    endpoint = local_controller.ensure_ready()
    # Then: the owned process is reaped and a fresh owned Chrome is launched.
    assert (first.terminated, first.waits, endpoint.process_id, len(launcher.commands)) == (1, 1, 202, 2)


def test_close_preserves_replacement_of_owned_port_snapshot(config: LocalBrowserGatewayConfig) -> None:
    # Given: a ready owned Chrome whose endpoint name is replaced before close.
    process = FakeProcess(101)
    launcher = FakeLauncher(config.profile_root, [process], [_payload()])
    local_controller = _controller(config, launcher, FakeProbe({(9222, "/devtools/browser/token")}))
    assert local_controller.ensure_ready().process_id == 101
    replacement = _payload(9223)
    port_file = config.profile_root / "DevToolsActivePort"
    port_file.unlink()
    port_file.write_bytes(replacement)
    port_file.chmod(0o600)
    # When: close performs guarded cleanup using its last owned snapshot.
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        local_controller.close()
    # Then: it reaps its process but leaves the unowned replacement untouched.
    assert raised.value.reason == "local_chrome_port_file_invalid"
    assert (process.terminated, process.waits, port_file.read_bytes()) == (1, 1, replacement)
