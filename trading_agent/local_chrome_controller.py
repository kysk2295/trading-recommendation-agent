from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Literal, NewType, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.local_browser_gateway_config import LocalBrowserGatewayConfig
from trading_agent.local_browser_private_fs import (
    InvalidLocalBrowserPrivateFsError,
    PrivateBrowserDirectory,
    PrivateBrowserFile,
    open_private_browser_directory,
    read_private_browser_file,
    unlink_private_browser_file,
)

ChromeDebugPort = NewType("ChromeDebugPort", int)
_PORT_FILE = "DevToolsActivePort"
_PORT_FILE_MAX_BYTES = 256
_PORT_FILE_TEXT = re.compile(r"([1-9][0-9]{0,4})\n(/devtools/browser/[A-Za-z0-9_-]{1,128})\n?\Z")


@dataclass(slots=True)
class InvalidLocalChromeControllerError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(slots=True)
class InvalidLocalChromeEndpointInvariantError(ValueError):
    reason: str = "local_chrome_endpoint_ownership_invalid"

    def __str__(self) -> str:
        return self.reason


class LocalChromeEndpoint(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    port: ChromeDebugPort
    browser_path: str
    browser_websocket_url: str
    ownership: Literal["owned", "attached"]
    process_id: int | None

    @model_validator(mode="after")
    def require_honest_ownership(self) -> LocalChromeEndpoint:
        if (self.ownership == "owned") != (self.process_id is not None):
            raise InvalidLocalChromeEndpointInvariantError()
        return self


class ChromeProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float) -> int: ...


class ChromeLauncher(Protocol):
    def launch(self, command: tuple[str, ...]) -> ChromeProcess: ...


class ChromeHealthProbe(Protocol):
    def probe(self, port: ChromeDebugPort, path: str) -> bool: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SubprocessChromeLauncher:
    def launch(self, command: tuple[str, ...]) -> ChromeProcess:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            shell=False,
        )


@dataclass(frozen=True, slots=True)
class _PortRecord:
    file: PrivateBrowserFile
    port: ChromeDebugPort
    browser_path: str


def chrome_launch_command(config: LocalBrowserGatewayConfig) -> tuple[str, ...]:
    return (
        str(config.chrome_executable),
        f"--user-data-dir={config.profile_root}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    )


class LocalChromeController:
    def __init__(
        self,
        config: LocalBrowserGatewayConfig,
        *,
        probe: ChromeHealthProbe,
        launcher: ChromeLauncher | None = None,
        clock: Clock | None = None,
        owner_id: int | None = None,
    ) -> None:
        self._config = config
        self._probe = probe
        self._launcher = launcher or SubprocessChromeLauncher()
        self._clock = clock or time
        self._owner_id = os.getuid() if owner_id is None else owner_id
        self._process: ChromeProcess | None = None

    def ensure_ready(self) -> LocalChromeEndpoint:
        try:
            with (
                open_private_browser_directory(self._config.state_root, self._owner_id),
                open_private_browser_directory(self._config.profile_root, self._owner_id) as profile,
            ):
                return self._ensure(profile)
        except InvalidLocalBrowserPrivateFsError as error:
            reason = "local_chrome_private_directory_invalid"
            if "directory" not in error.reason:
                reason = "local_chrome_port_file_invalid"
            raise InvalidLocalChromeControllerError(reason=reason) from None

    def close(self) -> None:
        self._stop_owned_process()

    def _ensure(self, profile: PrivateBrowserDirectory) -> LocalChromeEndpoint:
        raw = read_private_browser_file(profile, _PORT_FILE, self._owner_id, _PORT_FILE_MAX_BYTES)
        record = _parse_port_file(raw)
        process = self._process
        if raw is not None and record is None:
            if process is not None:
                self._stop_and_unlink(profile, raw)
            raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid")
        if process is not None:
            if record is not None and process.poll() is None and self._probe.probe(record.port, record.browser_path):
                return _endpoint(record, "owned", process.pid)
            self._stop_and_unlink(profile, raw)
            return self._launch_and_wait(profile)
        if record is not None:
            if self._probe.probe(record.port, record.browser_path):
                return _endpoint(record, "attached", None)
            raise InvalidLocalChromeControllerError(reason="local_chrome_endpoint_unavailable")
        return self._launch_and_wait(profile)

    def _launch_and_wait(self, profile: PrivateBrowserDirectory) -> LocalChromeEndpoint:
        try:
            self._process = self._launcher.launch(chrome_launch_command(self._config))
        except OSError:
            raise InvalidLocalChromeControllerError(reason="local_chrome_launch_failed") from None
        deadline = self._clock.monotonic() + self._config.startup_timeout_seconds
        while True:
            process = self._process
            if process is None:
                raise InvalidLocalChromeControllerError(reason="local_chrome_launch_failed")
            raw = read_private_browser_file(profile, _PORT_FILE, self._owner_id, _PORT_FILE_MAX_BYTES)
            record = _parse_port_file(raw)
            if process.poll() is not None:
                self._stop_and_unlink(profile, raw)
                raise InvalidLocalChromeControllerError(reason="local_chrome_early_exit")
            if raw is not None and record is None:
                self._stop_and_unlink(profile, raw)
                raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid")
            if record is not None and self._probe.probe(record.port, record.browser_path):
                return _endpoint(record, "owned", process.pid)
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                self._stop_and_unlink(profile, raw)
                raise InvalidLocalChromeControllerError(reason="local_chrome_startup_timeout")
            self._clock.sleep(min(0.1, remaining))

    def _stop_and_unlink(self, profile: PrivateBrowserDirectory, file: PrivateBrowserFile | None) -> None:
        self._stop_owned_process()
        if file is not None:
            unlink_private_browser_file(profile, _PORT_FILE, file, self._owner_id)

    def _stop_owned_process(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5.0)
        else:
            process.wait(timeout=5.0)


def _parse_port_file(file: PrivateBrowserFile | None) -> _PortRecord | None:
    if file is None:
        return None
    try:
        match = _PORT_FILE_TEXT.fullmatch(file.payload.decode("utf-8"))
    except UnicodeDecodeError:
        return None
    if match is None or int(match.group(1)) > 65535:
        return None
    return _PortRecord(file, ChromeDebugPort(int(match.group(1))), match.group(2))


def _endpoint(
    record: _PortRecord, ownership: Literal["owned", "attached"], process_id: int | None
) -> LocalChromeEndpoint:
    return LocalChromeEndpoint(
        port=record.port,
        browser_path=record.browser_path,
        browser_websocket_url=f"ws://127.0.0.1:{record.port}{record.browser_path}",
        ownership=ownership,
        process_id=process_id,
    )
