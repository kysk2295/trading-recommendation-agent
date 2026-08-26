from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol

from trading_agent.local_browser_gateway_config import LocalBrowserGatewayConfig
from trading_agent.local_browser_private_fs import (
    InvalidLocalBrowserPrivateFsError,
    PrivateBrowserDirectory,
    PrivateBrowserFile,
    open_private_browser_directory,
    read_private_browser_file,
    unlink_private_browser_file,
)
from trading_agent.local_browser_profile_lease import (
    InvalidLocalBrowserProfileLeaseError,
    LocalBrowserProfileLease,
    LocalBrowserProfileLeaseBusyError,
    acquire_local_browser_profile_lease,
)
from trading_agent.local_chrome_endpoint import ChromeDebugPort, LocalChromeEndpoint
from trading_agent.local_chrome_endpoint import (
    PortRecord as _PortRecord,
)
from trading_agent.local_chrome_endpoint import (
    local_chrome_endpoint as _endpoint,
)
from trading_agent.local_chrome_endpoint import (
    parse_port_file as _parse_port_file,
)

_PORT_FILE = "DevToolsActivePort"
_PORT_FILE_MAX_BYTES = 256


@dataclass(slots=True)
class InvalidLocalChromeControllerError(RuntimeError):
    reason: str

    def __str__(self) -> str: return self.reason


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
            umask=0o077,
        )


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
        self._owned_file: PrivateBrowserFile | None = None
        self._lease: LocalBrowserProfileLease | None = None

    def ensure_ready(self) -> LocalChromeEndpoint:
        try:
            with (
                open_private_browser_directory(self._config.state_root, self._owner_id),
                open_private_browser_directory(self._config.profile_root, self._owner_id) as profile,
            ):
                return self._ensure(profile)
        except (InvalidLocalBrowserPrivateFsError, InvalidLocalBrowserProfileLeaseError) as error:
            self._discard_owned_state()
            reason = "local_chrome_profile_lease_invalid"
            if isinstance(error, InvalidLocalBrowserPrivateFsError):
                reason = "local_chrome_private_directory_invalid"
                if "directory" not in error.reason:
                    reason = "local_chrome_port_file_invalid"
            raise InvalidLocalChromeControllerError(reason=reason) from None

    def close(self) -> None:
        file = self._owned_file
        if file is None and self._process is None:
            self._release_lease()
            return
        try:
            with open_private_browser_directory(self._config.profile_root, self._owner_id) as profile:
                self._require_owned_lease(profile)
                self._owned_file = None
                self._stop_owned_process()
                if file is not None:
                    unlink_private_browser_file(profile, _PORT_FILE, file, self._owner_id)
        except InvalidLocalBrowserProfileLeaseError:
            self._discard_owned_state()
            raise InvalidLocalChromeControllerError(reason="local_chrome_profile_lease_invalid") from None
        except InvalidLocalBrowserPrivateFsError:
            self._discard_owned_state()
            raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid") from None
        finally:
            self._release_lease()

    def _ensure(self, profile: PrivateBrowserDirectory) -> LocalChromeEndpoint:
        raw = read_private_browser_file(profile, _PORT_FILE, self._owner_id, _PORT_FILE_MAX_BYTES)
        record = _parse_port_file(raw)
        process = self._process
        if process is not None:
            self._require_owned_lease(profile)
        if raw is not None and record is None:
            if process is not None:
                self._stop_and_unlink(profile, raw)
            raise InvalidLocalChromeControllerError(reason="local_chrome_port_file_invalid")
        if process is not None:
            if record is not None and process.poll() is None and self._probe.probe(record.port, record.browser_path):
                return self._remember_owned_endpoint(profile, record, process)
            self._stop_and_unlink(profile, raw)
            return self._acquire_and_launch(profile)
        if record is not None:
            return self._attached(record)
        return self._acquire_and_launch(profile)

    def _acquire_and_launch(self, profile: PrivateBrowserDirectory) -> LocalChromeEndpoint:
        try:
            lease = acquire_local_browser_profile_lease(profile, self._owner_id)
        except LocalBrowserProfileLeaseBusyError:
            return self._attached_or_busy(profile)
        try:
            raw = read_private_browser_file(profile, _PORT_FILE, self._owner_id, _PORT_FILE_MAX_BYTES)
            record = _parse_port_file(raw)
            if raw is not None:
                lease.release()
                return self._attached(record)
            lease.require_current(profile)
            self._lease = lease
            return self._launch_and_wait(profile)
        except (
            InvalidLocalBrowserPrivateFsError,
            InvalidLocalBrowserProfileLeaseError,
            InvalidLocalChromeControllerError,
        ):
            if self._lease is None:
                lease.release()
            raise

    def _attached_or_busy(self, profile: PrivateBrowserDirectory) -> LocalChromeEndpoint:
        raw = read_private_browser_file(profile, _PORT_FILE, self._owner_id, _PORT_FILE_MAX_BYTES)
        record = _parse_port_file(raw)
        if record is not None:
            return self._attached(record)
        reason = "local_chrome_profile_busy" if raw is None else "local_chrome_port_file_invalid"
        raise InvalidLocalChromeControllerError(reason=reason)

    def _attached(self, record: _PortRecord | None) -> LocalChromeEndpoint:
        if record is not None and self._probe.probe(record.port, record.browser_path):
            return _endpoint(record, "attached", None)
        reason = "local_chrome_endpoint_unavailable" if record is not None else "local_chrome_port_file_invalid"
        raise InvalidLocalChromeControllerError(reason=reason)

    def _launch_and_wait(self, profile: PrivateBrowserDirectory) -> LocalChromeEndpoint:
        try:
            self._process = self._launcher.launch(chrome_launch_command(self._config))
        except OSError:
            self._release_lease()
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
                return self._remember_owned_endpoint(profile, record, process)
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                self._stop_and_unlink(profile, raw)
                raise InvalidLocalChromeControllerError(reason="local_chrome_startup_timeout")
            self._clock.sleep(min(0.1, remaining))

    def _stop_and_unlink(self, profile: PrivateBrowserDirectory, file: PrivateBrowserFile | None) -> None:
        self._require_owned_lease(profile)
        owned_file, self._owned_file = self._owned_file, None
        self._stop_owned_process()
        try:
            if owned_file is not None:
                unlink_private_browser_file(profile, _PORT_FILE, owned_file, self._owner_id)
            elif file is not None:
                unlink_private_browser_file(profile, _PORT_FILE, file, self._owner_id)
        finally:
            self._release_lease()

    def _remember_owned_endpoint(
        self, profile: PrivateBrowserDirectory, record: _PortRecord, process: ChromeProcess
    ) -> LocalChromeEndpoint:
        self._require_owned_lease(profile)
        self._owned_file = record.file
        return _endpoint(record, "owned", process.pid)

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

    def _release_lease(self) -> None:
        lease, self._lease = self._lease, None
        if lease is not None:
            lease.release()

    def _require_owned_lease(self, profile: PrivateBrowserDirectory) -> None:
        lease = self._lease
        if lease is None:
            raise InvalidLocalBrowserProfileLeaseError()
        lease.require_current(profile)

    def _discard_owned_state(self) -> None:
        self._owned_file = None
        self._stop_owned_process()
        self._release_lease()
