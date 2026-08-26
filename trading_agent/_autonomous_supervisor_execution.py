from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from threading import current_thread, main_thread
from time import monotonic
from typing import Final, Literal, Protocol

from pydantic import ValidationError

from trading_agent.autonomous_reasoning import (
    AUTONOMOUS_REASONING_RESPONSE_ADAPTER,
    AutonomousReasoningClient,
    AutonomousReasoningRequest,
    AutonomousReasoningResponse,
    AutonomousToolCall,
    AutonomousToolObservation,
    InvalidAutonomousReasoningError,
)
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousTaskId
from trading_agent.autonomous_tool_runtime import AutonomousToolRuntime, AutonomousToolRuntimeError

_ERROR: Final = b"E"
_OK: Final = b"O"
_CRASH: Final = b"X"
type Operation = Literal["reason", "tool"]


class AutonomousExecutionError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class AutonomousExecutionTimeoutError(AutonomousExecutionError):
    pass


class AutonomousExecutionCrash(BaseException):
    pass


class AutonomousToolDispatcher(Protocol):
    def dispatch(self, role: AutonomousAgentRole, call: AutonomousToolCall) -> AutonomousToolObservation: ...


@dataclass(frozen=True, slots=True)
class BoundedAutonomousExecution:
    reasoner: AutonomousReasoningClient
    tools: AutonomousToolRuntime
    timeout_seconds: float

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        payload = _call_in_worker("reason", self.reasoner, self.tools, request, None, None, self.timeout_seconds)
        try:
            return AUTONOMOUS_REASONING_RESPONSE_ADAPTER.validate_json(payload)
        except ValidationError:
            raise AutonomousExecutionError(reason="autonomous_reasoning_result_invalid") from None

    def dispatch(self, role: AutonomousAgentRole, call: AutonomousToolCall) -> AutonomousToolObservation:
        payload = _call_in_worker("tool", self.reasoner, self.tools, None, role, call, self.timeout_seconds)
        try:
            return AutonomousToolObservation.model_validate_json(payload)
        except ValidationError:
            raise AutonomousExecutionError(reason="autonomous_tool_result_invalid") from None


def _call_in_worker(
    operation: Operation,
    reasoner: AutonomousReasoningClient,
    tools: AutonomousToolRuntime,
    request: AutonomousReasoningRequest | None,
    role: AutonomousAgentRole | None,
    call: AutonomousToolCall | None,
    timeout: float,
) -> bytes:
    if current_thread() is not main_thread():
        raise AutonomousExecutionError(reason="autonomous_execution_main_thread_required")
    context = get_context("fork")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(send, operation, reasoner, tools, request, role, call))
    deadline = monotonic() + timeout
    try:
        process.start()
        send.close()
        if not receive.poll(max(0.0, deadline - monotonic())):
            process.terminate()
            process.join()
            raise AutonomousExecutionTimeoutError(reason=f"autonomous_{operation}_timeout")
        message = receive.recv_bytes()
        process.join(max(0.0, deadline - monotonic()))
        if process.is_alive():
            process.terminate()
            process.join()
    except EOFError:
        process.join()
        raise AutonomousExecutionCrash("autonomous_execution_worker_crashed") from None
    finally:
        receive.close()
        if process.is_alive():
            process.terminate()
            process.join()
    tag, payload = message[:1], message[1:]
    if tag == _OK:
        return payload
    if tag == _ERROR:
        raise AutonomousExecutionError(reason=payload.decode("ascii"))
    raise AutonomousExecutionCrash("autonomous_execution_worker_crashed")


def _worker(
    send: Connection,
    operation: Operation,
    reasoner: AutonomousReasoningClient,
    tools: AutonomousToolRuntime,
    request: AutonomousReasoningRequest | None,
    role: AutonomousAgentRole | None,
    call: AutonomousToolCall | None,
) -> None:
    try:
        if operation == "reason" and request is not None:
            response = reasoner.next_step(request)
            send.send_bytes(_OK + response.model_dump_json().encode("utf-8"))
        elif operation == "tool" and role is not None and call is not None:
            observation = tools.dispatch(role, call)
            send.send_bytes(_OK + observation.model_dump_json().encode("utf-8"))
        else:
            send.send_bytes(_ERROR + b"autonomous_execution_request_invalid")
    except (InvalidAutonomousReasoningError, AutonomousToolRuntimeError):
        send.send_bytes(_ERROR + f"autonomous_{operation}_failed".encode("ascii"))
    except Exception:  # noqa: RUF100 # noqa: BROAD_EXCEPT_OK: isolate untrusted provider and tool callback failures
        send.send_bytes(_ERROR + f"autonomous_{operation}_failed".encode("ascii"))
    except BaseException:  # noqa: RUF100 # noqa: BROAD_EXCEPT_OK: preserve process-crash semantics for restart replay
        send.send_bytes(_CRASH)
    finally:
        send.close()


@contextmanager
def task_execution_lease(database: Path, task_id: AutonomousTaskId) -> Iterator[bool]:
    descriptor = -1
    acquired = False
    lease_path = database.with_name(f".{database.name}.{task_id}.execution.lock")
    try:
        descriptor = os.open(
            lease_path,
            os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_RDWR,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        identity = os.fstat(descriptor)
        if not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid() or identity.st_mode & 0o077:
            raise AutonomousExecutionError(reason="autonomous_execution_lease_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    except OSError as error:
        raise AutonomousExecutionError(reason="autonomous_execution_lease_failed") from error
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        if descriptor >= 0:
            os.close(descriptor)


__all__ = (
    "AutonomousExecutionCrash",
    "AutonomousExecutionError",
    "AutonomousExecutionTimeoutError",
    "AutonomousToolDispatcher",
    "BoundedAutonomousExecution",
    "task_execution_lease",
)
