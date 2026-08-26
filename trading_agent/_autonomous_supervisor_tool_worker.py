from __future__ import annotations

import os
from multiprocessing.connection import Connection

from trading_agent._autonomous_supervisor_wire import ToolRuntimeWire, build_tools
from trading_agent.autonomous_reasoning import AutonomousToolCall
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolExecutionContext,
    AutonomousToolRuntimeError,
)

_CRASH = b"X"
_ENTERED = b"C"
_ERROR = b"E"
_OK = b"O"
_READY = b"R"


def tool_worker(
    send: Connection,
    tools_wire_spec: ToolRuntimeWire,
    role_value: str,
    call_json: str,
    task_id: str,
    agent_family_id: str,
    market_scope: str,
) -> None:
    try:
        os.setsid()
        send.send_bytes(_READY)
        tools = build_tools(tools_wire_spec)
        role = AutonomousAgentRole(role_value)
        call = AutonomousToolCall.model_validate_json(call_json)
        context = AutonomousToolExecutionContext.model_validate(
            {"task_id": task_id, "agent_family_id": agent_family_id, "market_scope": market_scope}
        )
        send.send_bytes(_ENTERED)
        observation = tools.dispatch(role, call, context)
        send.send_bytes(_OK + observation.model_dump_json().encode("utf-8"))
    except AutonomousToolRuntimeError:
        send.send_bytes(_ERROR + b"autonomous_tool_failed")
    except Exception:  # noqa: RUF100 # noqa: BROAD_EXCEPT_OK: isolate untrusted host-tool callback failures
        send.send_bytes(_ERROR + b"autonomous_tool_failed")
    except BaseException:  # noqa: RUF100 # noqa: BROAD_EXCEPT_OK: preserve process-crash semantics for restart replay
        send.send_bytes(_CRASH)
    finally:
        send.close()


__all__ = ("tool_worker",)
