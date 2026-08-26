from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
from functools import partial
from pathlib import Path

from tests.test_autonomous_task_models import NOW
from trading_agent.autonomous_reasoning import (
    AutonomousReasoningResponse,
    AutonomousToolArguments,
)
from trading_agent.autonomous_reasoning_codec import AutonomousStructuredReasoner
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext, AutonomousToolInvocationError
from trading_agent.researcher_llm import FixtureLlmProposalClient, HermesCliProposalClient

_EXECUTABLE = Path(__file__).parent / "fixtures" / "autonomous_reasoner"


class FixtureCrash(BaseException):
    pass


def fixture_reasoner(
    tmp_path: Path,
    responses: tuple[AutonomousReasoningResponse, ...],
    *,
    behavior: str = "responses",
    marker: Path | None = None,
    delay: float = 0.0,
    priority_routes: bool = False,
    timeout_seconds: float = 120.0,
) -> AutonomousStructuredReasoner:
    payload = json.dumps(
        {
            "behavior": behavior,
            "delay": delay,
            "marker": None if marker is None else str(marker),
            "priority_routes": priority_routes,
            "responses": tuple(response.model_dump_json() for response in responses),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    config = tmp_path / f"reasoner-{digest}.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(payload, encoding="utf-8")
    return AutonomousStructuredReasoner(
        HermesCliProposalClient(_EXECUTABLE, str(config), "fixture-provider", timeout_seconds=timeout_seconds)
    )


def fixture_client_reasoner(response: AutonomousReasoningResponse) -> AutonomousStructuredReasoner:
    return AutonomousStructuredReasoner(FixtureLlmProposalClient(response.model_dump_json().encode()))


def observed_tool(_args: AutonomousToolArguments, _context: AutonomousToolExecutionContext) -> str:
    return '{"status":"observed"}'


def tool_operation(
    _args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    behavior: str,
    primary: str = "",
    secondary: str = "",
) -> str:
    if behavior == "empty":
        return "{}"
    if behavior == "fail":
        raise AutonomousToolInvocationError(reason="fixture_tool_failure")
    if behavior == "crash":
        raise FixtureCrash
    if behavior == "hung":
        while not Path(primary).exists():
            time.sleep(0.005)
        Path(secondary).touch()
        return '{"status":"late"}'
    if behavior == "record":
        with Path(primary).open("a", encoding="utf-8") as stream:
            stream.write("invoked\n")
        time.sleep(0.1)
        return '{"status":"observed"}'
    if behavior == "context":
        Path(primary).write_text(context.task_id, encoding="ascii")
        return '{"status":"observed"}'
    if behavior == "descendant":
        program = "import sys,time;from pathlib import Path;time.sleep(1);Path(sys.argv[1]).touch()"
        child = subprocess.Popen((sys.executable, "-c", program, secondary))
        Path(primary).write_text(str(child.pid), encoding="ascii")
        while True:
            time.sleep(0.005)
    return observed_tool(_args, context)


def fixture_tool(behavior: str, *, primary: Path | None = None, secondary: Path | None = None):
    return partial(
        tool_operation,
        behavior=behavior,
        primary="" if primary is None else str(primary),
        secondary="" if secondary is None else str(secondary),
    )


def now_clock() -> dt.datetime:
    return NOW


def zero_clock() -> float:
    return 0.0


__all__ = (
    "fixture_client_reasoner",
    "fixture_reasoner",
    "fixture_tool",
    "now_clock",
    "observed_tool",
    "zero_clock",
)
