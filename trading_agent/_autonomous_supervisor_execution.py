from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from time import monotonic
from typing import Final, Literal, Protocol

from pydantic import ValidationError

from trading_agent._autonomous_supervisor_process import AutonomousExecutionError, reap_direct, reap_group
from trading_agent._autonomous_supervisor_wire import (
    ReasonerWire,
    ToolRuntimeWire,
    build_reasoner,
    build_tools,
    reasoner_wire,
    tools_wire,
)
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
from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)
from trading_agent.systematic_regime_store_file import open_private_file, require_private_file

_ERROR: Final = b"E"
_OK: Final = b"O"
_CRASH: Final = b"X"
_ENTERED: Final = b"C"
_READY: Final = b"R"
_STARTUP_SECONDS: Final = 5.0
type Operation = Literal["reason", "tool"]


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
    total_timeout_seconds: float = 120.0

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        payload = _call_in_worker(
            "reason", self.reasoner, self.tools, request, None, None, self.timeout_seconds, self.total_timeout_seconds
        )
        try:
            return AUTONOMOUS_REASONING_RESPONSE_ADAPTER.validate_json(payload)
        except ValidationError:
            raise AutonomousExecutionError(reason="autonomous_reasoning_result_invalid") from None

    def dispatch(self, role: AutonomousAgentRole, call: AutonomousToolCall) -> AutonomousToolObservation:
        payload = _call_in_worker(
            "tool", self.reasoner, self.tools, None, role, call, self.timeout_seconds, self.total_timeout_seconds
        )
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
    callback_timeout: float,
    total_timeout: float,
) -> bytes:
    total_deadline = monotonic() + total_timeout
    context = get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    if operation == "reason" and request is not None:
        target = _reason_worker
        arguments = (send, reasoner_wire(reasoner), request.model_dump_json())
    elif operation == "tool" and role is not None and call is not None:
        target = _tool_worker
        arguments = (send, tools_wire(tools), role.value, call.model_dump_json())
    else:
        raise AutonomousExecutionError(reason="autonomous_execution_request_invalid")
    process = context.Process(target=target, args=arguments)
    startup_deadline = min(total_deadline, monotonic() + _STARTUP_SECONDS)
    started = False
    reaped = False
    ready = False
    try:
        process.start()
        started = True
        send.close()
        if not receive.poll(max(0.0, startup_deadline - monotonic())) or receive.recv_bytes() != _READY:
            reaped = True
            reap_direct(process, 0.0)
            raise AutonomousExecutionError(reason="autonomous_execution_worker_not_ready")
        ready = True
        if not receive.poll(max(0.0, startup_deadline - monotonic())) or receive.recv_bytes() != _ENTERED:
            reaped = True
            reap_group(process, 0.0)
            raise AutonomousExecutionError(reason="autonomous_execution_callback_not_entered")
        callback_deadline = min(total_deadline, monotonic() + callback_timeout)
        if not receive.poll(max(0.0, callback_deadline - monotonic())):
            reaped = True
            reap_group(process, 0.0)
            raise AutonomousExecutionTimeoutError(reason=f"autonomous_{operation}_timeout")
        message = receive.recv_bytes()
        reaped = True
        reap_group(process, max(0.0, callback_deadline - monotonic()))
    except EOFError:
        if started and not reaped:
            reaped = True
            (reap_group if ready else reap_direct)(process, 0.0)
        raise AutonomousExecutionCrash("autonomous_execution_worker_crashed") from None
    finally:
        receive.close()
        send.close()
        if started and not reaped:
            (reap_group if ready else reap_direct)(process, 0.0)
    tag, payload = message[:1], message[1:]
    if tag == _OK:
        return payload
    if tag == _ERROR:
        raise AutonomousExecutionError(reason=payload.decode("ascii"))
    raise AutonomousExecutionCrash("autonomous_execution_worker_crashed")


def _reason_worker(
    send: Connection,
    reasoner_wire_spec: ReasonerWire,
    request_json: str,
) -> None:
    try:
        os.setsid()
        send.send_bytes(_READY)
        reasoner = build_reasoner(reasoner_wire_spec)
        request = AutonomousReasoningRequest.model_validate_json(request_json)
        send.send_bytes(_ENTERED)
        response = reasoner.next_step(request)
        send.send_bytes(_OK + response.model_dump_json().encode("utf-8"))
    except InvalidAutonomousReasoningError:
        send.send_bytes(_ERROR + b"autonomous_reason_failed")
    except Exception:  # noqa: RUF100 # noqa: BROAD_EXCEPT_OK: isolate untrusted provider and tool callback failures
        send.send_bytes(_ERROR + b"autonomous_reason_failed")
    except BaseException:  # noqa: RUF100 # noqa: BROAD_EXCEPT_OK: preserve process-crash semantics for restart replay
        send.send_bytes(_CRASH)
    finally:
        send.close()


def _tool_worker(
    send: Connection,
    tools_wire_spec: ToolRuntimeWire,
    role_value: str,
    call_json: str,
) -> None:
    try:
        os.setsid()
        send.send_bytes(_READY)
        tools = build_tools(tools_wire_spec)
        role = AutonomousAgentRole(role_value)
        call = AutonomousToolCall.model_validate_json(call_json)
        send.send_bytes(_ENTERED)
        observation = tools.dispatch(role, call)
        send.send_bytes(_OK + observation.model_dump_json().encode("utf-8"))
    except AutonomousToolRuntimeError:
        send.send_bytes(_ERROR + b"autonomous_tool_failed")
    except Exception:  # noqa: RUF100 # noqa: BROAD_EXCEPT_OK: isolate untrusted host-tool callback failures
        send.send_bytes(_ERROR + b"autonomous_tool_failed")
    except BaseException:  # noqa: RUF100 # noqa: BROAD_EXCEPT_OK: preserve process-crash semantics for restart replay
        send.send_bytes(_CRASH)
    finally:
        send.close()


@contextmanager
def task_execution_lease(database: Path, task_id: AutonomousTaskId) -> Iterator[bool]:
    parent = -1
    descriptor = -1
    acquired = False
    name = f".{database.name}.{task_id}.execution.lock"
    try:
        parent = open_private_parent(database.parent, create=False)
        require_private_directory(parent)
        require_open_directory_path(database.parent, parent)
        descriptor = _open_lease_file(parent, name)
        _require_lease_identity(parent, name, descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
        _require_lease_identity(parent, name, descriptor)
        require_open_directory_path(database.parent, parent)
    except (OSError, TypeError, ValueError) as error:
        raise AutonomousExecutionError(reason="autonomous_execution_lease_failed") from error
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


def _require_lease_identity(parent: int, name: str, descriptor: int) -> None:
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise AutonomousExecutionError(reason="autonomous_execution_lease_invalid")
    require_private_file(descriptor)


def _open_lease_file(parent: int, name: str) -> int:
    try:
        return open_private_file(parent, name, create=True, write=True)
    except FileExistsError:
        return open_private_file(parent, name, create=False, write=True)


__all__ = (
    "AutonomousExecutionCrash",
    "AutonomousExecutionError",
    "AutonomousExecutionTimeoutError",
    "AutonomousToolDispatcher",
    "BoundedAutonomousExecution",
    "task_execution_lease",
)
