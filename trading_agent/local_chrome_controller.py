from __future__ import annotations

import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NewType, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.local_browser_gateway_config import LocalBrowserGatewayConfig

ChromeDebugPort = NewType("ChromeDebugPort", int)
_PORT_FILE = "DevToolsActivePort"
_PORT_FILE_MAX_BYTES = 256
_PORT_FILE_TEXT = re.compile(r"([1-9][0-9]{0,4})\n(/devtools/browser/[A-Za-z0-9_-]{1,128})\n?\Z")


@dataclass(frozen=True, slots=True)
class InvalidLocalChromeControllerError(RuntimeError):
    reason: str

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
            raise ValueError("local_chrome_endpoint_ownership_invalid")
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


type _PortFile = tuple[ChromeDebugPort, str]


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
        launcher: ChromeLauncher | None = None,
        probe: ChromeHealthProbe,
        clock: Clock | None = None,
        owner_id: int | None = None,
    ) -> None:
        self._config = config
        self._launcher = launcher or SubprocessChromeLauncher()
        self._probe = probe
        self._clock = clock or time
        self._owner_id = os.getuid() if owner_id is None else owner_id
        self._process: ChromeProcess | None = None

    def ensure_ready(self) -> LocalChromeEndpoint:
        _prepare_private_directory(self._config.state_root, self._owner_id)
        _prepare_private_directory(self._config.profile_root, self._owner_id)
        process = self._process
        if process is not None:
            try:
                port_file = _read_port_file(self._config.profile_root, self._owner_id)
            except InvalidLocalChromeControllerError:
                self._stop_and_clear_port_file()
                raise
            if (
                process.poll() is None
                and port_file is not None
                and self._probe.probe(port_file[0], port_file[1])
            ):
                return _endpoint(port_file, "owned", process.pid)
            self._stop_and_clear_port_file()
        port_file = _read_port_file(self._config.profile_root, self._owner_id)
        if port_file is not None:
            if self._probe.probe(port_file[0], port_file[1]):
                return _endpoint(port_file, "attached", None)
            _remove_stale_port_file(self._config.profile_root, self._owner_id)
        return self._launch_and_wait()

    def close(self) -> None:
        self._stop_owned_process()

    def _launch_and_wait(self) -> LocalChromeEndpoint:
        try:
            self._process = self._launcher.launch(chrome_launch_command(self._config))
        except OSError:
            raise InvalidLocalChromeControllerError(reason="local_chrome_launch_failed") from None
        deadline = self._clock.monotonic() + self._config.startup_timeout_seconds
        while True:
            process = self._process
            if process is None:
                raise InvalidLocalChromeControllerError(reason="local_chrome_launch_failed")
            if process.poll() is not None:
                self._stop_and_clear_port_file()
                raise InvalidLocalChromeControllerError(reason="local_chrome_early_exit")
            try:
                port_file = _read_port_file(self._config.profile_root, self._owner_id)
            except InvalidLocalChromeControllerError:
                self._stop_and_clear_port_file()
                raise
            if port_file is not None and self._probe.probe(port_file[0], port_file[1]):
                return _endpoint(port_file, "owned", process.pid)
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                self._stop_and_clear_port_file()
                raise InvalidLocalChromeControllerError(reason="local_chrome_startup_timeout")
            self._clock.sleep(min(0.1, remaining))

    def _stop_and_clear_port_file(self) -> None:
        self._stop_owned_process()
        _remove_stale_port_file(self._config.profile_root, self._owner_id)

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


def _endpoint(
    port_file: _PortFile, ownership: Literal["owned", "attached"], process_id: int | None
) -> LocalChromeEndpoint:
    return LocalChromeEndpoint(
        port=port_file[0],
        browser_path=port_file[1],
        browser_websocket_url=f"ws://127.0.0.1:{port_file[0]}{port_file[1]}",
        ownership=ownership,
        process_id=process_id,
    )


def _prepare_private_directory(path: Path, owner_id: int) -> None:
    if _has_symlink_component(path):
        raise InvalidLocalChromeControllerError(reason="local_chrome_private_directory_invalid")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            current.chmod(0o700)
        else:
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise InvalidLocalChromeControllerError(reason="local_chrome_private_directory_invalid")
    try:
        metadata = os.lstat(path)
    except OSError:
        raise InvalidLocalChromeControllerError(reason="local_chrome_private_directory_invalid") from None
    if _has_symlink_component(path) or not stat.S_ISDIR(metadata.st_mode):
        raise InvalidLocalChromeControllerError(reason="local_chrome_private_directory_invalid")
    if metadata.st_uid != owner_id or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise InvalidLocalChromeControllerError(reason="local_chrome_private_directory_invalid")


def _read_port_file(profile_root: Path, owner_id: int) -> _PortFile | None:
    path = profile_root / _PORT_FILE
    metadata = _private_port_metadata(path, owner_id)
    if metadata is None:
        return None
    if metadata.st_size > _PORT_FILE_MAX_BYTES:
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid") from None
    try:
        checked = os.fstat(descriptor)
        if not _is_private_regular_file(checked, owner_id) or checked.st_size > _PORT_FILE_MAX_BYTES:
            raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid")
        payload = os.read(descriptor, _PORT_FILE_MAX_BYTES + 1)
    except OSError:
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid") from None
    finally:
        os.close(descriptor)
    if len(payload) > _PORT_FILE_MAX_BYTES:
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid")
    try:
        match = _PORT_FILE_TEXT.fullmatch(payload.decode("utf-8"))
    except UnicodeDecodeError:
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid") from None
    if match is None:
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid")
    port_text, browser_path = match.groups()
    if int(port_text) > 65535:
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid")
    return ChromeDebugPort(int(port_text)), browser_path


def _remove_stale_port_file(profile_root: Path, owner_id: int) -> None:
    path = profile_root / _PORT_FILE
    if _private_port_metadata(path, owner_id) is None:
        return
    try:
        path.unlink()
    except OSError:
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid") from None


def _private_port_metadata(path: Path, owner_id: int) -> os.stat_result | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid") from None
    if not _is_private_regular_file(metadata, owner_id):
        raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid")
    return metadata


def _is_private_regular_file(metadata: os.stat_result, owner_id: int) -> bool:
    return (
        metadata.st_uid == owner_id
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return True
        except FileNotFoundError:
            return False
        except OSError:
            return True
    return False
