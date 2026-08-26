from __future__ import annotations

import datetime as dt
from typing import Final, Literal

from trading_agent._autonomous_supervisor_steps import (
    FailurePayload,
    WaitPayload,
    payload_json,
    plain_step,
    run_budget,
    safe_payload,
    tick_result,
)
from trading_agent.autonomous_task_models import (
    AutonomousResearchTask,
    AutonomousSupervisorTickResult,
    AutonomousTaskState,
)
from trading_agent.autonomous_task_store import AutonomousTaskStore

_RETRY_DELAYS: Final = (15, 60, 240, 720)


def budget_wait(
    tasks: AutonomousTaskStore,
    task: AutonomousResearchTask,
    now: dt.datetime,
    model_calls: int,
    tool_calls: int,
) -> AutonomousSupervisorTickResult:
    wake = now + dt.timedelta(minutes=1)
    step = plain_step(
        task,
        len(tasks.reader().steps(task.task_id)) + 1,
        now,
        AutonomousTaskState.WAITING_TIME,
        payload_json(WaitPayload(cause="budget")),
        task.source_evidence_ids,
        task.evidence_refs,
        run_budget(model_calls, tool_calls, 0),
        wake,
    )
    with tasks.writer() as writer:
        _ = writer.append_step(step)
    return tick_result(tasks.reader().task(task.task_id) or task, "waiting", model_calls, tool_calls)


def lease_wait(task: AutonomousResearchTask, now: dt.datetime) -> AutonomousSupervisorTickResult:
    return AutonomousSupervisorTickResult(
        status="waiting",
        task_id=task.task_id,
        agent_family_id=task.agent_family_id,
        market_scope=task.market_scope,
        next_wake_at=now + dt.timedelta(seconds=1),
    )


def failure(
    tasks: AutonomousTaskStore,
    task: AutonomousResearchTask,
    now: dt.datetime,
    model_calls: int,
    tool_calls: int,
    source: Literal["reasoning", "tool", "memory", "supervisor"],
    reason: str,
    decision_hash: str | None,
) -> AutonomousSupervisorTickResult:
    steps = tasks.reader().steps(task.task_id)
    retry = 1 + sum(isinstance(safe_payload(step), FailurePayload) for step in steps)
    minutes = _RETRY_DELAYS[retry - 1] if retry <= len(_RETRY_DELAYS) else 1440
    wake = now + dt.timedelta(minutes=minutes)
    payload = FailurePayload(decision_hash=decision_hash, source=source, stable_reason=reason, retry_count=retry)
    step = plain_step(
        task,
        len(steps) + 1,
        now,
        AutonomousTaskState.BLOCKED,
        payload_json(payload),
        task.source_evidence_ids,
        task.evidence_refs,
        run_budget(model_calls, tool_calls, 0),
        wake,
        reason,
    )
    with tasks.writer() as writer:
        _ = writer.append_step(step)
    return tick_result(tasks.reader().task(task.task_id) or task, "blocked", model_calls, tool_calls)


__all__ = ("budget_wait", "failure", "lease_wait")
