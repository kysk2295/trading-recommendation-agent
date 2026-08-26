from __future__ import annotations

import os
import signal
import subprocess
from typing import Protocol


class ChromeProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float) -> int: ...


class ChromeLauncher(Protocol):
    def launch(self, command: tuple[str, ...]) -> ChromeProcess: ...


class _ProcessGroupChromeProcess:
    def __init__(self, process: ChromeProcess) -> None:
        self._process = process
        self.pid = process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    def terminate(self) -> None:
        os.killpg(self.pid, signal.SIGTERM)

    def kill(self) -> None:
        os.killpg(self.pid, signal.SIGKILL)

    def wait(self, timeout: float) -> int:
        return self._process.wait(timeout)


class SubprocessChromeLauncher:
    def launch(self, command: tuple[str, ...]) -> ChromeProcess:
        return _ProcessGroupChromeProcess(
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                shell=False,
                umask=0o077,
            )
        )
