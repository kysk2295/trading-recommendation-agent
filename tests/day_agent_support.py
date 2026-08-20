from __future__ import annotations

import datetime as dt

from trading_agent.day_agent_task_models import (
    DayAgentAction,
    DayAgentBudget,
    DayAgentResearchTask,
    DayAgentTaskState,
    DayAgentTaskStep,
)

NOW = dt.datetime(2026, 8, 21, 14, 30, tzinfo=dt.UTC)


def day_task(
    *,
    task_id: str = "task-20260821-NVDA",
    state: DayAgentTaskState = DayAgentTaskState.OPEN,
    budget: DayAgentBudget | None = None,
) -> DayAgentResearchTask:
    scheduled_wake_at = NOW + dt.timedelta(minutes=5) if state is DayAgentTaskState.WAITING else None
    terminal_reason = "research_complete" if state is DayAgentTaskState.COMPLETED else None
    if state is DayAgentTaskState.BLOCKED:
        terminal_reason = "market_data_unavailable"
    return DayAgentResearchTask(
        task_id=task_id,
        objective="Assess whether NVDA has a current-session researchable catalyst.",
        question="Does the available current-session evidence justify another bounded action?",
        current_hypothesis="A verified catalyst may support continued leader strength.",
        falsification_conditions=("catalyst_refuted", "leader_loses_relative_strength"),
        open_questions=("Which verified catalyst is current?",),
        resume_condition="Resume when a current-session observation is available.",
        state=state,
        evidence_refs=("evidence.market.001",),
        budget=budget or DayAgentBudget(
            remaining_model_calls=4,
            remaining_tool_calls=8,
            remaining_runtime_seconds=60,
        ),
        created_at=NOW,
        updated_at=NOW,
        scheduled_wake_at=scheduled_wake_at,
        terminal_reason=terminal_reason,
    )


def day_step(
    task: DayAgentResearchTask,
    *,
    sequence: int,
    action: DayAgentAction,
    state: DayAgentTaskState | None = None,
    budget: DayAgentBudget | None = None,
    evidence_refs: tuple[str, ...] | None = None,
    occurred_at: dt.datetime = NOW,
) -> DayAgentTaskStep:
    resulting_state = task.state if state is None else state
    scheduled_wake_at = NOW + dt.timedelta(minutes=5) if resulting_state is DayAgentTaskState.WAITING else None
    terminal_reason = "research_complete" if resulting_state is DayAgentTaskState.COMPLETED else None
    if resulting_state is DayAgentTaskState.BLOCKED:
        terminal_reason = "budget_exhausted"
    return DayAgentTaskStep(
        task_id=task.task_id,
        sequence=sequence,
        action=action,
        reason="The bounded action preserves durable research lineage.",
        evidence_refs=task.evidence_refs if evidence_refs is None else evidence_refs,
        budget=task.budget if budget is None else budget,
        state=resulting_state,
        occurred_at=occurred_at,
        scheduled_wake_at=scheduled_wake_at,
        terminal_reason=terminal_reason,
    )
