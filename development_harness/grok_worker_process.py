from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_DEFAULT_MAX_STDOUT_BYTES: Final = 1_048_576
_POLL_SECONDS: Final = 0.05
_KILL_WAIT_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class WorkerProcessResult:
    returncode: int
    stdout: bytes


class WorkerProcessError(RuntimeError):
    """Raised when a worker process cannot be executed safely."""


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _reap_process(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_KILL_WAIT_SECONDS)


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if process.pid is None:
        return
    _kill_process_group(process.pid)
    _reap_process(process)
    if process.pid is not None:
        _kill_process_group(process.pid)


def run_worker_process(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
    max_stdout_bytes: int = _DEFAULT_MAX_STDOUT_BYTES,
) -> WorkerProcessResult:
    """Run a worker with file-backed stdout bounds and process-group kill."""

    if not command:
        raise WorkerProcessError("worker command is empty")
    if timeout_seconds <= 0:
        raise WorkerProcessError("worker timeout must be positive")
    if max_stdout_bytes <= 0:
        raise WorkerProcessError("stdout bound must be positive")

    stdout_path: Path | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="grok-worker-",
            suffix=".stdout",
            delete=False,
        ) as handle:
            stdout_path = Path(handle.name)
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=handle,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except OSError as error:
        if stdout_path is not None:
            with contextlib.suppress(OSError):
                stdout_path.unlink()
        raise WorkerProcessError("failed to start worker process") from error

    assert process is not None
    assert stdout_path is not None
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    oversized = False
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            try:
                if stdout_path.stat().st_size > max_stdout_bytes:
                    oversized = True
                    break
            except OSError:
                oversized = True
                break
            time.sleep(_POLL_SECONDS)
        if timed_out or oversized:
            _terminate_worker(process)
            if timed_out:
                raise TimeoutError("worker process timed out")
            raise WorkerProcessError("worker stdout exceeded the configured bound")
        _reap_process(process)
        if process.pid is not None:
            _kill_process_group(process.pid)
        try:
            size = stdout_path.stat().st_size
        except OSError as error:
            raise WorkerProcessError("worker stdout is unreadable") from error
        if size > max_stdout_bytes:
            raise WorkerProcessError("worker stdout exceeded the configured bound")
        stdout = stdout_path.read_bytes()
        returncode = 0 if process.returncode is None else process.returncode
        return WorkerProcessResult(returncode=returncode, stdout=stdout)
    finally:
        if process is not None and process.poll() is None:
            _terminate_worker(process)
        with contextlib.suppress(OSError):
            stdout_path.unlink()
