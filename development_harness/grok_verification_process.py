"""Process-group isolation for independent offline verification commands."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from development_harness.grok_process_env import sanitize_git_routing_environ

_POLL_SECONDS: Final = 0.05
_KILL_WAIT_SECONDS: Final = 5.0


class VerificationProcessError(RuntimeError):
    """Raised when independent verification cannot execute safely."""


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _reap_process(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_KILL_WAIT_SECONDS)


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    if process.pid is None:
        return
    _kill_process_group(process.pid)
    _reap_process(process)
    if process.pid is not None:
        _kill_process_group(process.pid)


def run_verification_process(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> int:
    """Run one verification command in a new process group and reap descendants.

    The caller supplies the prepared environment (for example cache-disabled settings).
    This module only strips ambient ``GIT_*`` keys and does not import verification helpers.
    Ordinary background children that remain in the process group are killed on success,
    failure, and timeout. Descendants that call ``setsid`` remain residual risk without
    an OS sandbox.
    """

    if not command:
        raise VerificationProcessError("verification command is empty")
    if timeout_seconds <= 0:
        raise VerificationProcessError("verification timeout must be positive")
    process_env = sanitize_git_routing_environ(base=env)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=process_env,
        )
    except OSError as error:
        raise VerificationProcessError("failed to start verification process") from error

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(_POLL_SECONDS)
        if timed_out:
            _terminate_group(process)
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout_seconds)
        _reap_process(process)
        if process.pid is not None:
            _kill_process_group(process.pid)
        return 0 if process.returncode is None else process.returncode
    finally:
        if process is not None and process.poll() is None:
            _terminate_group(process)
        elif process is not None and process.pid is not None:
            _kill_process_group(process.pid)
