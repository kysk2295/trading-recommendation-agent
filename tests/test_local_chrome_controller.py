from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import trading_agent.local_browser_private_fs as private_fs
import trading_agent.local_chrome_controller as chrome
from trading_agent.local_browser_gateway_config import LocalBrowserGatewayConfig


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class FakeProcess:
    """Mutable fixture records process lifecycle effects."""

    pid: int
    exit_code: int | None = None
    waits: int = 0
    terminated: int = 0
    killed: int = 0
    timeout_waits: int = 0

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1

    def wait(self, timeout: float) -> int:
        self.waits += 1
        if self.timeout_waits:
            self.timeout_waits -= 1
            raise subprocess.TimeoutExpired("chrome", timeout)
        self.exit_code = 0
        return self.exit_code


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class FakeLauncher:
    """Mutable fixture records launches and publishes test port files."""

    profile: Path
    processes: list[FakeProcess]
    payloads: list[bytes | None]
    commands: list[tuple[str, ...]] = field(default_factory=list)

    def launch(self, command: tuple[str, ...]) -> FakeProcess:
        self.commands.append(command)
        payload = self.payloads.pop(0)
        if payload is not None:
            port_file = self.profile / "DevToolsActivePort"
            port_file.write_bytes(payload)
            port_file.chmod(0o600)
        return self.processes.pop(0)


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class FakeProbe:
    """Mutable fixture records deterministic health checks."""

    healthy: set[tuple[int, str]]
    calls: list[tuple[int, str]] = field(default_factory=list)

    def probe(self, port: chrome.ChromeDebugPort, path: str) -> bool:
        value = (int(port), path)
        self.calls.append(value)
        return value in self.healthy


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class FakeClock:
    """Mutable fixture advances injected monotonic time without sleeping."""

    value: float = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def config(tmp_path: Path) -> LocalBrowserGatewayConfig:
    return LocalBrowserGatewayConfig(
        project_root=(tmp_path / "project").absolute(),
        uv_path=(tmp_path / "uv").absolute(),
        chrome_executable=(tmp_path / "Chrome").absolute(),
        state_root=(tmp_path / "private" / "state").absolute(),
        profile_root=(tmp_path / "private" / "profile").absolute(),
        socket_path=(tmp_path / "private" / "state" / "gateway.sock").absolute(),
        receipt_database=(tmp_path / "private" / "state" / "receipts.sqlite3").absolute(),
        screenshot_root=(tmp_path / "private" / "state" / "screenshots").absolute(),
        startup_timeout_seconds=1.0,
    )


def _payload(port: int = 9222, path: str = "/devtools/browser/token") -> bytes:
    return f"{port}\n{path}\n".encode()


def _controller(
    config: LocalBrowserGatewayConfig, launcher: FakeLauncher, probe: FakeProbe, clock: FakeClock | None = None
) -> chrome.LocalChromeController:
    return chrome.LocalChromeController(config, launcher=launcher, probe=probe, clock=clock or FakeClock())


@dataclass(frozen=True, slots=True)
class PopenCall:
    command: tuple[str, ...]
    stdin: int
    stdout: int
    stderr: int
    start_new_session: bool
    shell: bool


def test_subprocess_launcher_uses_exact_command_and_safe_popen_flags(
    monkeypatch: pytest.MonkeyPatch, config: LocalBrowserGatewayConfig
) -> None:
    # Given: the production launcher and a Popen recorder.
    calls: list[PopenCall] = []

    def popen(
        command: tuple[str, ...], *, stdin: int, stdout: int, stderr: int, start_new_session: bool, shell: bool
    ) -> FakeProcess:
        calls.append(PopenCall(command, stdin, stdout, stderr, start_new_session, shell))
        return FakeProcess(31337)

    monkeypatch.setattr(chrome.subprocess, "Popen", popen)
    command = chrome.chrome_launch_command(config)
    # When: Chrome is launched.
    process = chrome.SubprocessChromeLauncher().launch(command)
    # Then: it gets only the dedicated-profile invocation and detached null streams.
    assert process.pid == 31337
    assert command == (
        str(config.chrome_executable), f"--user-data-dir={config.profile_root}", "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check", "about:blank",
    )
    assert calls == [PopenCall(command, subprocess.DEVNULL, subprocess.DEVNULL, subprocess.DEVNULL, True, False)]


def test_ready_creates_exact_private_directories_and_reuses_owned_process(config: LocalBrowserGatewayConfig) -> None:
    # Given: absent private directories and a healthy launched process.
    process = FakeProcess(101)
    launcher = FakeLauncher(config.profile_root, [process], [_payload()])
    controller = _controller(config, launcher, FakeProbe({(9222, "/devtools/browser/token")}))
    # When: readiness is ensured twice.
    first = controller.ensure_ready()
    second = controller.ensure_ready()
    # Then: roots are private and the original owned endpoint is reused.
    modes = stat.S_IMODE(os.lstat(config.state_root).st_mode), stat.S_IMODE(os.lstat(config.profile_root).st_mode)
    assert modes == (0o700, 0o700)
    assert (first, second, len(launcher.commands)) == (second, first, 1)
    assert first.ownership == "owned" and first.process_id == 101


@pytest.mark.parametrize(
    "payload,mode",
    (
        (b"0\n/devtools/browser/token\n", 0o600), (_payload(path="/bad"), 0o600), (_payload() + b"extra\n", 0o600),
        (_payload().replace(b"\n", b"\r\n"), 0o600), (_payload().replace(b"\n", b"\r"), 0o600),
        (_payload().replace(b"\n", b"\v"), 0o600), (_payload().replace(b"\n", b"\f"), 0o600), (_payload(), 0o644),
        (b"9" * 300, 0o600),
    ),
)
def test_ready_rejects_invalid_port_file(config: LocalBrowserGatewayConfig, payload: bytes, mode: int) -> None:
    # Given: private roots and a malformed DevToolsActivePort file.
    config.state_root.mkdir(parents=True, mode=0o700)
    config.profile_root.mkdir(mode=0o700)
    port_file = config.profile_root / "DevToolsActivePort"
    port_file.write_bytes(payload)
    port_file.chmod(mode)
    launcher = FakeLauncher(config.profile_root, [], [])
    # When: readiness reads the endpoint file.
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = _controller(config, launcher, FakeProbe(set())).ensure_ready()
    # Then: malformed, weak, or oversized input cannot trigger a launch.
    assert raised.value.reason == "local_chrome_port_file_invalid"
    assert launcher.commands == []


def test_ready_attaches_healthy_existing_endpoint_and_close_does_not_terminate(
    config: LocalBrowserGatewayConfig,
) -> None:
    # Given: a valid endpoint belonging to another controller.
    config.state_root.mkdir(parents=True, mode=0o700)
    config.profile_root.mkdir(mode=0o700)
    (config.profile_root / "DevToolsActivePort").write_bytes(_payload())
    (config.profile_root / "DevToolsActivePort").chmod(0o600)
    launcher = FakeLauncher(config.profile_root, [], [])
    controller = _controller(config, launcher, FakeProbe({(9222, "/devtools/browser/token")}))
    # When: it is made ready then closed.
    endpoint = controller.ensure_ready()
    controller.close()
    # Then: it reports honest attachment and never starts or kills Chrome.
    assert endpoint.ownership == "attached" and endpoint.process_id is None
    assert launcher.commands == []


def test_dead_owned_process_and_stale_endpoint_each_cause_one_safe_restart(config: LocalBrowserGatewayConfig) -> None:
    # Given: an owned Chrome that later dies, followed by a stale unhealthy endpoint.
    first, replacement = FakeProcess(101), FakeProcess(202)
    launcher = FakeLauncher(config.profile_root, [first, replacement], [_payload(9222), _payload(9223)])
    probe = FakeProbe({(9222, "/devtools/browser/token"), (9223, "/devtools/browser/token")})
    controller = _controller(config, launcher, probe)
    assert controller.ensure_ready().process_id == 101
    first.exit_code = 1
    # When: readiness detects the dead owned process.
    endpoint = controller.ensure_ready()
    # Then: it reaps only its old process and returns the one replacement it owns.
    assert (endpoint.process_id, first.waits, len(launcher.commands)) == (202, 1, 2)


def test_malformed_owned_port_file_reaps_owned_process_without_replacement(config: LocalBrowserGatewayConfig) -> None:
    # Given: an owned healthy Chrome whose port file becomes malformed.
    process = FakeProcess(101)
    launcher = FakeLauncher(config.profile_root, [process], [_payload()])
    controller = _controller(config, launcher, FakeProbe({(9222, "/devtools/browser/token")}))
    assert controller.ensure_ready().process_id == 101
    port_file = config.profile_root / "DevToolsActivePort"
    port_file.write_bytes(_payload().replace(b"\n", b"\r\n"))
    port_file.chmod(0o600)
    # When: the owned endpoint file can no longer be parsed.
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = controller.ensure_ready()
    # Then: only the owned process is reaped and its exact stale file is removed.
    assert raised.value.reason == "local_chrome_port_file_invalid"
    assert (process.terminated, process.waits, len(launcher.commands), port_file.exists()) == (1, 1, 1, False)


def test_ready_rejects_profile_component_swap_before_launch(
    config: LocalBrowserGatewayConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: path verification that swaps the freshly pinned profile directory.
    original = private_fs.require_open_directory_path

    def replace_profile(path: Path, descriptor: int) -> None:
        path.rename(path.with_name("profile-original"))
        path.mkdir(mode=0o700)
        original(path, descriptor)

    monkeypatch.setattr(private_fs, "require_open_directory_path", replace_profile)
    launcher = FakeLauncher(config.profile_root, [FakeProcess(101)], [_payload()])
    # When: readiness creates and verifies private roots.
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = _controller(config, launcher, FakeProbe(set())).ensure_ready()
    # Then: descriptor identity rejects the replacement before launch.
    assert raised.value.reason == "local_chrome_private_directory_invalid" and launcher.commands == []


def test_owned_cleanup_preserves_replaced_port_file(
    config: LocalBrowserGatewayConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an owned endpoint whose name is replaced immediately before cleanup.
    process = FakeProcess(101)
    launcher = FakeLauncher(config.profile_root, [process], [_payload()])
    probe = FakeProbe({(9222, "/devtools/browser/token")})
    controller = _controller(config, launcher, probe)
    assert controller.ensure_ready().process_id == 101
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
        _ = controller.ensure_ready()
    # Then: the replacement remains and no second Chrome starts.
    assert raised.value.reason == "local_chrome_port_file_invalid"
    observed = (
        process.terminated,
        process.waits,
        len(launcher.commands),
        (config.profile_root / "DevToolsActivePort").read_bytes(),
    )
    assert observed == (1, 1, 1, replacement)


def test_unhealthy_attached_endpoint_remains_unowned_and_unmodified(config: LocalBrowserGatewayConfig) -> None:
    # Given: a valid-but-unhealthy port file from an unowned Chrome.
    config.state_root.mkdir(parents=True, mode=0o700)
    config.profile_root.mkdir(mode=0o700)
    port_file = config.profile_root / "DevToolsActivePort"
    port_file.write_bytes(_payload(9222))
    port_file.chmod(0o600)
    launcher = FakeLauncher(config.profile_root, [], [])
    # When: readiness cannot health-check the attached endpoint.
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = _controller(config, launcher, FakeProbe(set())).ensure_ready()
    # Then: unavailable attachment is neither removed nor replaced.
    assert raised.value.reason == "local_chrome_endpoint_unavailable"
    assert launcher.commands == [] and port_file.read_bytes() == _payload(9222)


@pytest.mark.parametrize("process,payload,reason", ((FakeProcess(101, exit_code=1), None, "local_chrome_early_exit"),
                                                      (FakeProcess(102), None, "local_chrome_startup_timeout")))
def test_failed_startup_reaps_only_launched_process(
    config: LocalBrowserGatewayConfig, process: FakeProcess, payload: bytes | None, reason: str
) -> None:
    # Given: a launch that exits immediately or never publishes an endpoint.
    launcher = FakeLauncher(config.profile_root, [process], [payload])
    # When: bounded readiness fails.
    with pytest.raises(chrome.InvalidLocalChromeControllerError) as raised:
        _ = _controller(config, launcher, FakeProbe(set())).ensure_ready()
    # Then: the exact launched process is reaped and the reason is stable.
    assert raised.value.reason == reason
    assert process.waits >= 1 and (process.terminated == 1 if process.pid == 102 else process.terminated == 0)


def test_close_terminates_then_kills_only_owned_process_and_is_idempotent(config: LocalBrowserGatewayConfig) -> None:
    # Given: a ready owned process whose first wait times out.
    process = FakeProcess(101, timeout_waits=1)
    launcher = FakeLauncher(config.profile_root, [process], [_payload()])
    controller = _controller(config, launcher, FakeProbe({(9222, "/devtools/browser/token")}))
    assert controller.ensure_ready().process_id == 101
    # When: close is called twice.
    controller.close()
    controller.close()
    # Then: termination escalates only for the process this controller launched.
    assert (process.terminated, process.killed, process.waits) == (1, 1, 2)
