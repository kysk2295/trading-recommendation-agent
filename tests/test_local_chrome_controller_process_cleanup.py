from __future__ import annotations

import signal
import subprocess
from dataclasses import dataclass, field

import pytest

import trading_agent.local_chrome_controller as chrome
import trading_agent.local_chrome_process as chrome_process


@dataclass(slots=True)  # noqa: MUTABLE_OK — fake process records direct signal attempts
class RecordingPopen:
    pid: int = 31337
    direct_signals: list[str] = field(default_factory=list)

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.direct_signals.append("terminate")

    def kill(self) -> None:
        self.direct_signals.append("kill")

    def wait(self, timeout: float) -> int:
        return 0


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK — fake process records deterministic lifecycle calls
class ExitingGroupPopen:
    pid: int = 31337
    timeout_waits: int = 0
    poll_calls: int = 0
    waits: int = 0

    def poll(self) -> int | None:
        self.poll_calls += 1
        return None

    def terminate(self) -> None:
        raise AssertionError("process-group wrapper must not signal only the direct child")

    def kill(self) -> None:
        raise AssertionError("process-group wrapper must not signal only the direct child")

    def wait(self, timeout: float) -> int:
        self.waits += 1
        if self.timeout_waits >= self.waits:
            raise subprocess.TimeoutExpired("chrome", timeout)
        return 0


def _missing_process_group(_process_group: int, _sig: signal.Signals) -> None:
    raise ProcessLookupError


def _permission_denied(_process_group: int, _sig: signal.Signals) -> None:
    raise PermissionError


def test_subprocess_launcher_signals_only_its_new_chrome_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = RecordingPopen()
    signals: list[tuple[int, signal.Signals]] = []

    def popen(
        command: tuple[str, ...],
        *,
        stdin: int,
        stdout: int,
        stderr: int,
        start_new_session: bool,
        shell: bool,
        umask: int,
    ) -> RecordingPopen:
        assert start_new_session is True
        return process

    monkeypatch.setattr(chrome_process.subprocess, "Popen", popen)
    monkeypatch.setattr(chrome_process.os, "killpg", lambda process_group, sig: signals.append((process_group, sig)))

    owned = chrome.SubprocessChromeLauncher().launch(("Chrome",))
    owned.terminate()
    owned.kill()

    assert signals == [(31337, signal.SIGTERM), (31337, signal.SIGKILL)]
    assert process.direct_signals == []


def test_owned_cleanup_reaps_when_live_process_group_disappears_before_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a controller's live owned child whose process group exits before TERM.
    process = ExitingGroupPopen()
    controller = object.__new__(chrome.LocalChromeController)
    controller._process = chrome_process._ProcessGroupChromeProcess(process)
    monkeypatch.setattr(chrome_process.os, "killpg", _missing_process_group)
    # When: controller cleanup stops the owned process.
    controller._stop_owned_process()
    # Then: disappearance is already-stopped, so cleanup reaps the child and clears ownership.
    assert (process.poll_calls, process.waits, controller._process) == (1, 1, None)


def test_owned_cleanup_reaps_when_live_process_group_disappears_before_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a live owned child whose TERM wait times out and group exits before KILL.
    process = ExitingGroupPopen(timeout_waits=1)
    controller = object.__new__(chrome.LocalChromeController)
    controller._process = chrome_process._ProcessGroupChromeProcess(process)
    monkeypatch.setattr(chrome_process.os, "killpg", _missing_process_group)
    # When: controller cleanup escalates its timed-out stop.
    controller._stop_owned_process()
    # Then: the missing group does not block the final reap or owned-state cleanup.
    assert (process.poll_calls, process.waits, controller._process) == (2, 2, None)


def test_process_group_signal_preserves_permission_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an owned process group whose signal is denied by the operating system.
    process = ExitingGroupPopen()
    owned = chrome_process._ProcessGroupChromeProcess(process)
    monkeypatch.setattr(chrome_process.os, "killpg", _permission_denied)
    # When: the wrapper attempts TERM.
    with pytest.raises(PermissionError):
        owned.terminate()
    # Then: only a missing group is tolerated; permission failures remain visible.
