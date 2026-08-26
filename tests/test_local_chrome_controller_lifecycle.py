from __future__ import annotations

import os
from pathlib import Path

import pytest

import trading_agent.local_chrome_controller as chrome
from trading_agent.local_browser_gateway_config import LocalBrowserGatewayConfig
from trading_agent.local_browser_private_fs import (
    InvalidLocalBrowserPrivateFsError,
    PrivateBrowserDirectory,
    PrivateBrowserFile,
)
from trading_agent.local_browser_profile_lease import LOCAL_BROWSER_PROFILE_LEASE_NAME, LocalBrowserProfileLease


class Process:
    def __init__(self, pid: int) -> None:
        self.pid, self.exit_code, self.terminated, self.waits = pid, None, 0, 0

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.exit_code = 9

    def wait(self, timeout: float) -> int:
        self.waits += 1
        self.exit_code = 0
        return 0


class Launcher:
    def __init__(self, profile: Path, process: Process, payload: bytes | None) -> None:
        self.profile, self.process, self.payload, self.commands = profile, process, payload, []

    def launch(self, command: tuple[str, ...]) -> Process:
        self.commands.append(command)
        if self.payload is not None:
            path = self.profile / "DevToolsActivePort"
            path.write_bytes(self.payload)
            path.chmod(0o600)
        return self.process


class Probe:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    def probe(self, port: chrome.ChromeDebugPort, path: str) -> bool:
        return self.healthy


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def config(tmp_path: Path) -> LocalBrowserGatewayConfig:
    private = (tmp_path / "private").absolute()
    return LocalBrowserGatewayConfig(
        project_root=(tmp_path / "project").absolute(),
        uv_path=(tmp_path / "uv").absolute(),
        chrome_executable=(tmp_path / "Chrome").absolute(),
        state_root=private / "state",
        profile_root=private / "profile",
        socket_path=private / "state" / "gateway.sock",
        receipt_database=private / "state" / "receipts.sqlite3",
        screenshot_root=private / "state" / "shots",
        startup_timeout_seconds=1.0,
    )


def _payload(port: int = 9222) -> bytes:
    return f"{port}\n/devtools/browser/token\n".encode()


def _controller(
    config: LocalBrowserGatewayConfig, launcher: Launcher, healthy: bool = True
) -> chrome.LocalChromeController:
    return chrome.LocalChromeController(config, launcher=launcher, probe=Probe(healthy), clock=Clock())


def test_post_launch_private_fs_read_error_reaps_exact_process_without_unlinking_replacement(
    config: LocalBrowserGatewayConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    process, payload = Process(101), _payload(9223)
    launcher = Launcher(config.profile_root, process, payload)
    controller = _controller(config, launcher)
    original = chrome.read_private_browser_file
    calls = 0

    def fail_after_launch(
        directory: PrivateBrowserDirectory, name: str, owner_id: int, maximum_bytes: int
    ) -> PrivateBrowserFile | None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid")
        return original(directory, name, owner_id, maximum_bytes)

    def reject_unlink(
        directory: PrivateBrowserDirectory, name: str, expected: PrivateBrowserFile, owner_id: int
    ) -> None:
        raise AssertionError("post-launch read failure must not unlink an unknown endpoint")

    monkeypatch.setattr(chrome, "read_private_browser_file", fail_after_launch)
    monkeypatch.setattr(chrome, "unlink_private_browser_file", reject_unlink)
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = controller.ensure_ready()
    assert raised.value.reason == "local_chrome_port_file_invalid"
    assert (process.terminated, process.waits) == (1, 1)
    assert (config.profile_root / "DevToolsActivePort").read_bytes() == payload


def test_second_controller_real_flock_does_not_launch_when_owner_lease_is_held(
    config: LocalBrowserGatewayConfig,
) -> None:
    first_process = Process(101)
    first = _controller(config, Launcher(config.profile_root, first_process, _payload()))
    assert first.ensure_ready().ownership == "owned"
    (config.profile_root / "DevToolsActivePort").unlink()
    second_launcher = Launcher(config.profile_root, Process(202), _payload(9223))
    second = _controller(config, second_launcher)
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = second.ensure_ready()
    assert raised.value.reason == "local_chrome_profile_busy" and second_launcher.commands == []
    with pytest.raises(chrome.InvalidLocalChromeControllerError):
        first.close()
    assert first_process.terminated == 1


def test_endpoint_published_after_lease_before_reread_is_attached_and_preserved(
    config: LocalBrowserGatewayConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    delayed, launcher = _payload(9224), Launcher(config.profile_root, Process(101), _payload())
    original = chrome.acquire_local_browser_profile_lease

    def publish_after_lease(directory: PrivateBrowserDirectory, owner_id: int) -> LocalBrowserProfileLease:
        lease = original(directory, owner_id)
        path = config.profile_root / "DevToolsActivePort"
        path.write_bytes(delayed)
        path.chmod(0o600)
        return lease

    monkeypatch.setattr(chrome, "acquire_local_browser_profile_lease", publish_after_lease)
    controller = _controller(config, launcher)
    endpoint = controller.ensure_ready()
    controller.close()
    assert endpoint.ownership == "attached" and endpoint.process_id is None
    assert launcher.commands == [] and (config.profile_root / "DevToolsActivePort").read_bytes() == delayed


def test_owned_reuse_reaps_invalidated_lease_without_unlinking_endpoint(
    config: LocalBrowserGatewayConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an owned Chrome whose held lease name is replaced after readiness.
    process, payload = Process(101), _payload()
    first = _controller(config, Launcher(config.profile_root, process, payload))
    assert first.ensure_ready().ownership == "owned"
    lease = first._lease
    assert lease is not None
    descriptor = lease.descriptor
    lock = config.profile_root / LOCAL_BROWSER_PROFILE_LEASE_NAME
    lock.unlink()
    lock.write_bytes(b"")
    lock.chmod(0o600)

    def reject_unlink(
        directory: PrivateBrowserDirectory, name: str, expected: PrivateBrowserFile, owner_id: int
    ) -> None:
        raise AssertionError("invalidated lease must not unlink an endpoint")

    monkeypatch.setattr(chrome, "unlink_private_browser_file", reject_unlink)
    # When: readiness tries to reuse the owned process after the lease swap.
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = first.ensure_ready()
    # Then: the process and lease are released, while the endpoint is retained for attachment.
    assert raised.value.reason == "local_chrome_profile_lease_invalid"
    assert (process.terminated, process.waits, first._owned_file) == (1, 1, None)
    assert (config.profile_root / "DevToolsActivePort").read_bytes() == payload
    with pytest.raises(OSError):
        os.fstat(descriptor)
    second_launcher = Launcher(config.profile_root, Process(202), _payload(9223))
    second = _controller(config, second_launcher)
    attached = second.ensure_ready()
    assert attached.ownership == "attached" and attached.process_id is None and second_launcher.commands == []
