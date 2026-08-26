from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import run_local_browser_gateway as cli
from run_local_browser_gateway import main
from trading_agent.chrome_devtools_types import ChromeDevToolsStatus
from trading_agent.local_browser_dispatch import BrowserClient
from trading_agent.local_browser_gateway import (
    BrowserDispatchDependencies,
    BrowserRequestDispatcher,
    LocalBrowserGateway,
)
from trading_agent.local_browser_gateway_config import LocalBrowserGatewayConfig
from trading_agent.local_browser_gateway_wire import BrowserRequest
from trading_agent.local_browser_protocol import (
    BrowserCaptureRequest,
    BrowserOpenRequest,
    BrowserPageObservation,
    BrowserReadRequest,
    BrowserResponse,
    BrowserScreenshotReceipt,
    BrowserStatusRequest,
)
from trading_agent.local_browser_receipts import LocalBrowserReceiptStore
from trading_agent.local_browser_socket import LocalBrowserSocketClient, LocalBrowserSocketServer
from trading_agent.local_chrome_endpoint import ChromeDebugPort, LocalChromeEndpoint

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def short_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="browser-cli-", dir="/private/tmp") as value:
        root = Path(value)
        root.chmod(0o700)
        yield root


def _fixture_config(root: Path) -> tuple[LocalBrowserGatewayConfig, Path, Path]:
    project = root / "project"
    project.mkdir()
    (project / "run_local_browser_gateway.py").write_text("pass\n", encoding="utf-8")
    binaries = root / "bin"
    binaries.mkdir()
    uv_path, chrome = binaries / "uv", binaries / "chrome"
    for executable in (uv_path, chrome):
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
    private = root / "private"
    private.mkdir(mode=0o700)
    state, profile = root / "runtime" / "state", root / "runtime" / "profile"
    config = LocalBrowserGatewayConfig(
        project_root=project,
        uv_path=uv_path,
        chrome_executable=chrome,
        state_root=state,
        profile_root=profile,
        socket_path=state / "browser.sock",
        receipt_database=state / "receipts.sqlite3",
        screenshot_root=state / "screenshots",
    )
    return config, private / "gateway.json", private / "gateway.plist"


def _provision_args(config: LocalBrowserGatewayConfig, config_path: Path, plist_path: Path) -> tuple[str, ...]:
    options = {
        "project-root": config.project_root,
        "uv-path": config.uv_path,
        "chrome-executable": config.chrome_executable,
        "state-root": config.state_root,
        "profile-root": config.profile_root,
        "socket-path": config.socket_path,
        "receipt-database": config.receipt_database,
        "screenshot-root": config.screenshot_root,
        "config": config_path,
        "plist": plist_path,
    }
    return ("provision", *(part for name, value in options.items() for part in (f"--{name}", str(value))))


def test_help_exposes_only_gateway_operator_commands(capsys: pytest.CaptureFixture[str]) -> None:
    # Given: the gateway operator entrypoint.
    # When: its top-level help is requested.
    result = main(("--help",))
    # Then: only the five operator commands are advertised.
    output = capsys.readouterr().out
    assert result == 0
    assert all(command in output for command in ("provision", "verify", "run", "status", "activate"))
    assert "login" not in output and "download" not in output


def test_status_rejects_missing_private_config(short_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Given: an absent private config.
    # When: status crosses the config boundary.
    result = main(("status", "--config", str(short_root / "missing.json")))
    # Then: it fails redacted without creating an artifact.
    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == "" and "Traceback" not in captured.err
    assert not (short_root / "missing.json").exists()


def test_provision_and_verify_publish_redacted_canonical_contract(
    short_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: valid executable bindings and private output parents.
    config, config_path, plist_path = _fixture_config(short_root)
    # When: the operator provisions and verifies the contract.
    provisioned = main(_provision_args(config, config_path, plist_path))
    first = json.loads(capsys.readouterr().out)
    verified = main(("verify", "--config", str(config_path), "--plist", str(plist_path)))
    second = json.loads(capsys.readouterr().out)
    # Then: both surfaces expose digests and no configured paths.
    assert (provisioned, verified) == (0, 0)
    assert first == second
    assert first["status"] == "verified" and first["broker_mutation"] == 0
    assert all(
        str(path) not in json.dumps(first)
        for path in config.model_dump(mode="python").values()
        if isinstance(path, Path)
    )


def test_activate_verifies_authority_before_exact_launchctl(short_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a verified current-main contract and an injected launchctl runner.
    config, config_path, plist_path = _fixture_config(short_root)
    assert main(_provision_args(config, config_path, plist_path)) == 0
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        cli,
        "current_main_commit",
        lambda repository: "a" * 40 if repository == config.project_root else "",
    )
    # When: activation succeeds.
    result = main(
        ("activate", "--config", str(config_path), "--plist", str(plist_path)),
        runner=lambda command: calls.append(command) or 0,
    )
    # Then: exact bootstrap and kickstart commands are issued in order.
    domain = f"gui/{os.getuid()}"
    assert result == 0
    assert calls == [
        ("/bin/launchctl", "bootstrap", domain, str(plist_path)),
        ("/bin/launchctl", "kickstart", f"{domain}/{config.label}"),
    ]


def test_activate_boots_out_only_just_added_plist_when_kickstart_fails(
    short_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a verified current-main contract whose kickstart fails.
    config, config_path, plist_path = _fixture_config(short_root)
    assert main(_provision_args(config, config_path, plist_path)) == 0
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(cli, "current_main_commit", lambda _repository: "a" * 40)

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        return int(command[1] == "kickstart")

    # When: activation executes the transaction.
    result = main(("activate", "--config", str(config_path), "--plist", str(plist_path)), runner=runner)
    # Then: it rolls back that exact plist and reports failure.
    assert result == 2
    assert calls[-1] == ("/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path))


class NoDispatchGateway:
    def handle_bytes(self, payload: bytes) -> bytes:
        raise AssertionError(payload)


def test_run_returns_busy_when_gateway_socket_lease_is_held(short_root: Path) -> None:
    # Given: a provisioned gateway whose real service lease is held.
    config, config_path, plist_path = _fixture_config(short_root)
    assert main(_provision_args(config, config_path, plist_path)) == 0
    # When: a second run tries to acquire the same lease.
    with LocalBrowserSocketServer(config.socket_path, NoDispatchGateway()):
        result = main(("run", "--config", str(config_path)))
    # Then: the operator receives the dedicated busy exit code without launching Chrome.
    assert result == 3


class CountingController:
    def __init__(self) -> None:
        self.calls = 0

    def ensure_ready(self) -> LocalChromeEndpoint:
        self.calls += 1
        return LocalChromeEndpoint(
            port=ChromeDebugPort(9222),
            browser_path="/devtools/browser/fixture",
            browser_websocket_url="ws://127.0.0.1:9222/devtools/browser/fixture",
            ownership="attached",
            process_id=None,
        )


class FixtureCdpClient:
    def status(self) -> ChromeDevToolsStatus:
        return ChromeDevToolsStatus(ready=True, active_page_count=1)

    def open(self, url: str, *, captured_at: datetime) -> BrowserPageObservation:
        return BrowserPageObservation(target_id="page-1", url=url, title="Fixture", captured_at=captured_at)

    def read(self, target_id: str, *, captured_at: datetime) -> BrowserPageObservation:
        return BrowserPageObservation(
            target_id=target_id,
            url="https://example.com/story",
            title="Fixture",
            visible_text="bounded fixture text",
            captured_at=captured_at,
        )

    def capture(self, target_id: str, root: Path, *, captured_at: datetime) -> BrowserScreenshotReceipt:
        _ = target_id
        root.mkdir(mode=0o700)
        path = root / "fixture.png"
        path.write_bytes(b"fixture-png")
        path.chmod(0o600)
        return BrowserScreenshotReceipt(path=str(path), sha256="f" * 64, width=1, height=1, captured_at=captured_at)

    def search(self, query: str, *, captured_at: datetime) -> BrowserPageObservation:
        return self.open(f"https://example.com/{query}", captured_at=captured_at)

    def follow(self, target_id: str, link_index: int, *, captured_at: datetime) -> BrowserPageObservation:
        return self.read(f"{target_id}-{link_index}", captured_at=captured_at)


class FixtureCdpFactory:
    def create(self, endpoint: LocalChromeEndpoint) -> BrowserClient:
        assert endpoint.port == 9222
        return FixtureCdpClient()


def _round_trips(
    root: Path, controller: CountingController, requests: tuple[BrowserRequest, ...]
) -> tuple[BrowserResponse, ...]:
    errors: list[Exception] = []
    ready = threading.Event()

    def serve() -> None:
        try:
            dependencies = BrowserDispatchDependencies(controller, FixtureCdpFactory(), lambda: NOW)
            with (
                LocalBrowserReceiptStore(root / "receipts.sqlite3") as store,
                LocalBrowserSocketServer(
                    root / "browser.sock",
                    LocalBrowserGateway(store, BrowserRequestDispatcher(dependencies, root / "screenshots")),
                ) as server,
            ):
                ready.set()
                for _request in requests:
                    server.serve_once()
        except Exception as error:  # noqa: RUF100  # noqa: BROAD_EXCEPT_OK: fixture returns failures
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(timeout=2.0)
    client = LocalBrowserSocketClient(root / "browser.sock", timeout_seconds=1.0)
    responses: list[BrowserResponse] = []
    for request in requests:
        response = client.request(request)
        assert response.status == "ok"
        responses.append(response)
    thread.join(timeout=2.0)
    assert not thread.is_alive() and errors == []
    return tuple(responses)


def test_fixture_gateway_e2e_replays_exact_request_after_restart_without_second_dispatch(short_root: Path) -> None:
    # Given: fake Chrome/CDP behind the real gateway client, socket, and receipt database.
    state = short_root / "state"
    requests = (
        BrowserStatusRequest(request_id="a" * 64),
        BrowserOpenRequest(request_id="b" * 64, url="https://example.com/story"),
        BrowserReadRequest(request_id="c" * 64, target_id="page-1"),
        BrowserCaptureRequest(request_id="d" * 64, target_id="page-1"),
    )
    first, restarted = CountingController(), CountingController()
    # When: four actions run and the exact read is replayed after server restart.
    responses = _round_trips(state, first, requests)
    replay = _round_trips(state, restarted, (requests[2],))
    # Then: every first-run action dispatched, while replay never reached Chrome/CDP.
    assert first.calls == 4 and restarted.calls == 0
    assert responses[0].status_payload is not None and responses[0].status_payload.ready
    assert responses[1].observation is not None and responses[1].observation.target_id == "page-1"
    assert responses[2].observation is not None and responses[2].observation.visible_text == "bounded fixture text"
    assert responses[3].screenshot is not None and responses[3].screenshot.sha256 == "f" * 64
    assert replay == (responses[2],)
    assert (state / "screenshots" / "fixture.png").stat().st_mode & 0o777 == 0o600
