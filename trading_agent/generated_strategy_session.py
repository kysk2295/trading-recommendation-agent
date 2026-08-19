from __future__ import annotations

import hashlib
import os
import resource
import selectors
import signal
import subprocess
import time
from functools import partial
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self

from trading_agent.generated_strategy_execution import (
    GeneratedStrategyExecutionError,
    GeneratedStrategyLimits,
)
from trading_agent.generated_strategy_protocol import (
    MAX_FRAME_BYTES,
    NoSignalResponse,
    RunnerFailure,
    RunnerFrame,
    RunnerReady,
    SignalResponse,
    encode_frame,
    observe_request,
    parse_runner_frame,
    signal_from_response,
)
from trading_agent.generated_strategy_runtime import GeneratedStrategyRuntimeIdentity
from trading_agent.generated_strategy_source import require_generated_strategy_session_source
from trading_agent.models import BarInput, MomentumCandidate, StrategySignal


class GeneratedStrategySession:
    __slots__ = (
        "_buffer",
        "_limits",
        "_process",
        "_sequence",
        "_signal_hashes",
        "_stderr",
        "name",
    )

    name: str
    _process: subprocess.Popen[bytes]
    _stderr: BinaryIO
    _limits: GeneratedStrategyLimits
    _sequence: int
    _signal_hashes: list[str]
    _buffer: bytearray

    def __init__(
        self,
        name: str,
        process: subprocess.Popen[bytes],
        stderr_handle: BinaryIO,
        limits: GeneratedStrategyLimits,
    ) -> None:
        self.name = name
        self._process = process
        self._stderr = stderr_handle
        self._limits = limits
        self._sequence = 0
        self._signal_hashes = []
        self._buffer = bytearray()

    @classmethod
    def start(
        cls,
        artifact_id: str,
        source_path: Path,
        source_sha256: str,
        runtime: GeneratedStrategyRuntimeIdentity,
        limits: GeneratedStrategyLimits,
        session_root: Path,
        profile: str,
        runner: Path,
    ) -> Self:
        stderr_handle = (session_root / "stderr.log").open("wb")
        (session_root / "stderr.log").chmod(0o600)
        command = (
            str(runtime.sandbox_executable),
            "-p",
            profile,
            str(runtime.python_executable),
            "-I",
            str(runner),
            str(source_path.resolve(strict=True)),
        )
        environment = {
            "HOME": str(session_root / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "TMPDIR": str(session_root / "tmp"),
        }
        try:
            require_generated_strategy_session_source(source_path, source_sha256)
            process = subprocess.Popen(
                command,
                cwd=session_root,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_handle,
                start_new_session=True,
                preexec_fn=partial(_apply_limits, limits),
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            stderr_handle.close()
            raise
        session = cls(f"generated-python:{artifact_id}", process, stderr_handle, limits)
        try:
            ready = session._read_frame()
            if not isinstance(ready, RunnerReady):
                reason = ready.reason if isinstance(ready, RunnerFailure) else "runner_handshake_invalid"
                raise GeneratedStrategyExecutionError(reason)
            return session
        except (GeneratedStrategyExecutionError, OSError, ValueError):
            session.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def observe(
        self,
        bar: BarInput,
        candidate: MomentumCandidate | None,
    ) -> StrategySignal | None:
        self._sequence += 1
        request = observe_request(self._sequence, bar, candidate)
        pipe = self._process.stdin
        if pipe is None:
            raise GeneratedStrategyExecutionError("runner_stdin_unavailable")
        try:
            pipe.write(encode_frame(request))
            pipe.flush()
            response = self._read_frame()
            if not isinstance(response, NoSignalResponse | SignalResponse | RunnerFailure):
                raise GeneratedStrategyExecutionError("runner_response_invalid")
            self._signal_hashes.append(hashlib.sha256(encode_frame(response)).hexdigest())
            return signal_from_response(request, response, self.name)
        except GeneratedStrategyExecutionError:
            raise
        except (BrokenPipeError, OSError, ValueError) as error:
            raise GeneratedStrategyExecutionError("generated_strategy_protocol_failed") from error

    @property
    def signal_stream_sha256(self) -> str:
        return hashlib.sha256("".join(self._signal_hashes).encode()).hexdigest()

    def close(self) -> None:
        pipe = self._process.stdin
        if pipe is not None and not pipe.closed:
            pipe.close()
        try:
            self._process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            _terminate_group(self._process)
        if not self._stderr.closed:
            self._stderr.close()

    def _read_frame(self) -> RunnerFrame:
        stdout = self._process.stdout
        if stdout is None:
            raise GeneratedStrategyExecutionError("runner_stdout_unavailable")
        deadline = time.monotonic() + self._limits.wall_seconds
        with selectors.DefaultSelector() as selector:
            selector.register(stdout, selectors.EVENT_READ)
            while b"\n" not in self._buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    _terminate_group(self._process)
                    raise GeneratedStrategyExecutionError("frame_timeout")
                if not selector.select(min(remaining, 0.05)):
                    if _rss_bytes(self._process.pid) > self._limits.rss_bytes:
                        _terminate_group(self._process)
                        raise GeneratedStrategyExecutionError("rss_limit_exceeded")
                    continue
                chunk = os.read(stdout.fileno(), 4096)
                if not chunk:
                    raise GeneratedStrategyExecutionError("runner_exited")
                self._buffer.extend(chunk)
                if len(self._buffer) > MAX_FRAME_BYTES:
                    raise GeneratedStrategyExecutionError("frame_too_large")
        newline = self._buffer.index(b"\n") + 1
        payload = bytes(self._buffer[:newline])
        del self._buffer[:newline]
        return parse_runner_frame(payload)


def _apply_limits(limits: GeneratedStrategyLimits) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.output_bytes, limits.output_bytes))


def _rss_bytes(process_id: int) -> int:
    completed = subprocess.run(
        ("/bin/ps", "-o", "rss=", "-p", str(process_id)),
        check=False,
        capture_output=True,
        text=True,
        timeout=0.5,
        env={"LANG": "C", "LC_ALL": "C", "PATH": ""},
    )
    try:
        return int(completed.stdout.strip()) * 1024
    except ValueError:
        return 0


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=0.2)
    except (OSError, subprocess.TimeoutExpired):
        _kill_group(process)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        return
