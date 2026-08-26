from __future__ import annotations

import signal
from dataclasses import dataclass, field

import pytest

import trading_agent.local_chrome_controller as chrome


@dataclass(slots=True)
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

    monkeypatch.setattr(chrome.subprocess, "Popen", popen)
    monkeypatch.setattr(chrome.os, "killpg", lambda process_group, sig: signals.append((process_group, sig)))

    owned = chrome.SubprocessChromeLauncher().launch(("Chrome",))
    owned.terminate()
    owned.kill()

    assert signals == [(31337, signal.SIGTERM), (31337, signal.SIGKILL)]
    assert process.direct_signals == []
