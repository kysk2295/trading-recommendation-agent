from __future__ import annotations

import os
import signal
from multiprocessing.process import BaseProcess
from typing import Final

_REAP_GRACE_SECONDS: Final = 0.05


class AutonomousExecutionError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def reap_group(process: BaseProcess, initial_wait: float) -> None:
    process.join(initial_wait)
    _signal_group(process.pid, signal.SIGTERM)
    process.join(_REAP_GRACE_SECONDS)
    _signal_group(process.pid, signal.SIGKILL)
    process.join(_REAP_GRACE_SECONDS)
    _close_reaped(process)


def reap_direct(process: BaseProcess, initial_wait: float) -> None:
    process.join(initial_wait)
    if process.is_alive():
        process.terminate()
        process.join(_REAP_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(_REAP_GRACE_SECONDS)
    _close_reaped(process)


def _close_reaped(process: BaseProcess) -> None:
    if process.is_alive():
        raise AutonomousExecutionError(reason="autonomous_execution_worker_reap_failed")
    process.close()


def _signal_group(group: int | None, requested: signal.Signals) -> None:
    if group is None:
        return
    try:
        os.killpg(group, requested)
    except ProcessLookupError:
        return


__all__ = ("AutonomousExecutionError", "reap_direct", "reap_group")
