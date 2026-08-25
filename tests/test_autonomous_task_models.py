from __future__ import annotations

import datetime as dt
import json

import pytest
from pydantic import ValidationError

from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousRunBudget,
    AutonomousSupervisorTickResult,
    AutonomousTaskState,
    AutonomousTaskStep,
    InvalidAutonomousTaskFieldError,
    autonomous_step_id,
    autonomous_step_payload,
    autonomous_task_id,
    validate_autonomous_step_projection,
)
from trading_agent.research_agent_cycle_models import EvidenceId

NOW = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.UTC)
ROOT = EvidenceId("a" * 64)
OTHER = EvidenceId("b" * 64)


def budget() -> AutonomousRunBudget:
    return AutonomousRunBudget(
        remaining_model_calls=12,
        remaining_tool_calls=24,
        remaining_runtime_seconds=300,
    )


def task_fixture(**updates: object) -> AutonomousResearchTask:
    payload: dict[str, object] = {
        "task_id": autonomous_task_id("day_trading", "kr_equities", ROOT),
        "goal": "Observe the market and preserve a bounded research plan.",
        "owner_role": AutonomousAgentRole.SUPERVISOR,
        "agent_family_id": "day_trading",
        "market_scope": "kr_equities",
        "state": AutonomousTaskState.QUEUED,
        "priority": 50,
        "root_source_evidence_id": ROOT,
        "source_evidence_ids": (ROOT,),
        "evidence_refs": ("evidence:a",),
        "current_plan": ("observe_market",),
        "agent_version": "supervisor-v1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(updates)
    return AutonomousResearchTask.model_validate(payload)


def step_fixture(**updates: object) -> AutonomousTaskStep:
    payload: dict[str, object] = {
        "task_id": autonomous_task_id("day_trading", "kr_equities", ROOT),
        "sequence": 1,
        "role": AutonomousAgentRole.MARKET_OBSERVER,
        "agent_family_id": "day_trading",
        "market_scope": "kr_equities",
        "root_source_evidence_id": ROOT,
        "agent_version": "supervisor-v1",
        "state": AutonomousTaskState.OBSERVING,
        "source_evidence_ids": (ROOT,),
        "evidence_refs": ("evidence:a",),
        "budget": budget(),
        "occurred_at": NOW,
    }
    payload.update(updates)
    return AutonomousTaskStep.model_validate(payload)


def test_run_budget_accepts_bounds_and_rejects_outside_bounds() -> None:
    assert budget().remaining_model_calls == 12
    assert AutonomousRunBudget(
        remaining_model_calls=0,
        remaining_tool_calls=0,
        remaining_runtime_seconds=0,
    ).remaining_runtime_seconds == 0
    for field, value in (
        ("remaining_model_calls", -1),
        ("remaining_model_calls", 13),
        ("remaining_tool_calls", -1),
        ("remaining_tool_calls", 25),
        ("remaining_runtime_seconds", -1),
        ("remaining_runtime_seconds", 301),
    ):
        values = {
            "remaining_model_calls": 12,
            "remaining_tool_calls": 24,
            "remaining_runtime_seconds": 300,
        }
        values[field] = value
        with pytest.raises(ValidationError):
            AutonomousRunBudget(**values)


def test_waiting_time_and_blocked_require_one_wake_selector() -> None:
    for state in (AutonomousTaskState.WAITING_TIME, AutonomousTaskState.BLOCKED):
        with pytest.raises(ValidationError, match="future_wake_selector_required"):
            task_fixture(state=state, blocked_reason="source unavailable")
        with pytest.raises(ValidationError, match="future_wake_selector_required"):
            task_fixture(
                state=state,
                next_wake_at=NOW + dt.timedelta(minutes=5),
                next_wake_event="new_evidence",
                blocked_reason="source unavailable",
            )


def test_waiting_event_is_nonterminal_and_no_action_needs_a_wake() -> None:
    task = task_fixture(state=AutonomousTaskState.WAITING_EVENT, next_wake_event="market_evidence")
    assert task.terminal_reason is None
    assert task.state not in {AutonomousTaskState.COMPLETED, AutonomousTaskState.ABANDONED}
    with pytest.raises(ValidationError, match="terminal_fields_invalid"):
        task_fixture(
            state=AutonomousTaskState.COMPLETED,
            completed_actions=("no_action",),
            terminal_reason="nothing to do",
        )


def test_task_identity_binds_family_market_and_root_evidence() -> None:
    first = autonomous_task_id("day_trading", "kr_equities", ROOT)
    assert first == autonomous_task_id("day_trading", "kr_equities", ROOT)
    assert first != autonomous_task_id("day_trading", "kr_equities", OTHER)
    assert first != autonomous_task_id("swing_trading", "kr_equities", ROOT)
    with pytest.raises(ValidationError, match="task_id_identity_mismatch"):
        task_fixture(task_id=first, root_source_evidence_id=OTHER, source_evidence_ids=(OTHER,))


def test_refs_are_sorted_unique_and_root_is_immutable() -> None:
    with pytest.raises(ValidationError, match="sorted_unique_evidence_refs_required"):
        task_fixture(evidence_refs=("z", "a"))
    with pytest.raises(ValidationError, match="sorted_unique_subject_refs_required"):
        task_fixture(subject_refs=("same", "same"))
    with pytest.raises(ValidationError, match="root_source_evidence_required"):
        task_fixture(source_evidence_ids=(OTHER,))
    task = task_fixture(
        source_evidence_ids=(ROOT, OTHER),
        evidence_refs=("evidence:a", "evidence:b"),
        subject_refs=("symbol:005930",),
        working_memory_ids=("memory:a",),
        completed_actions=("observe_market",),
        pending_actions=("wait_for_event",),
    )
    assert task.source_evidence_ids == (ROOT, OTHER)


def test_timestamps_are_aware_and_normalized_to_utc() -> None:
    offset = dt.timezone(dt.timedelta(hours=9))
    task = task_fixture(
        created_at=dt.datetime(2026, 8, 26, 21, tzinfo=offset),
        updated_at=dt.datetime(2026, 8, 26, 21, 1, tzinfo=offset),
    )
    assert task.created_at.tzinfo is dt.UTC
    assert task.updated_at.tzinfo is dt.UTC
    with pytest.raises(ValidationError):
        task_fixture(created_at=dt.datetime(2026, 8, 26, 12))


def test_terminal_reason_and_wake_invariants() -> None:
    for state in (AutonomousTaskState.COMPLETED, AutonomousTaskState.ABANDONED):
        with pytest.raises(ValidationError, match="terminal_fields_invalid"):
            task_fixture(state=state)
        with pytest.raises(ValidationError, match="terminal_fields_invalid"):
            task_fixture(state=state, terminal_reason="done", next_wake_event="later")
        task = task_fixture(state=state, terminal_reason="done")
        assert task.next_wake_at is None
    with pytest.raises(ValidationError, match="active_fields_invalid"):
        task_fixture(state=AutonomousTaskState.OBSERVING, terminal_reason="not terminal")


def test_step_payload_is_canonical_and_step_id_deterministic() -> None:
    step = AutonomousTaskStep(
        task_id=autonomous_task_id("day_trading", "kr_equities", ROOT),
        sequence=1,
        role=AutonomousAgentRole.MARKET_OBSERVER,
        agent_family_id="day_trading",
        market_scope="kr_equities",
        root_source_evidence_id=ROOT,
        agent_version="supervisor-v1",
        state=AutonomousTaskState.OBSERVING,
        payload_json=json.dumps({"symbol": "005930", "price": 70000}, separators=(",", ":"), sort_keys=True),
        source_evidence_ids=(ROOT,),
        evidence_refs=("evidence:a",),
        budget=budget(),
        occurred_at=NOW,
    )
    assert step.step_id == autonomous_step_id(step)
    assert autonomous_step_payload(step) == autonomous_step_payload(step)
    tampered = step.model_dump()
    tampered["step_id"] = "0" * 64
    with pytest.raises(ValidationError, match="step_id_mismatch"):
        AutonomousTaskStep.model_validate(tampered)


def test_step_projection_rejects_immutable_authority_rewrites() -> None:
    task = task_fixture()
    step = step_fixture()
    validate_autonomous_step_projection(task, step)
    for field, value in (
        ("agent_version", "supervisor-v2"),
        ("root_source_evidence_id", OTHER),
        ("agent_family_id", "swing_trading"),
        ("market_scope", "us_equities"),
    ):
        altered = step.model_copy(update={field: value})
        with pytest.raises(InvalidAutonomousTaskFieldError, match="step_projection_authority_mismatch"):
            validate_autonomous_step_projection(task, altered)


def test_tick_result_covers_statuses_identity_and_wake_rules() -> None:
    task_id = autonomous_task_id("day_trading", "kr_equities", ROOT)
    identity = {"task_id": task_id, "agent_family_id": "day_trading", "market_scope": "kr_equities"}
    assert AutonomousSupervisorTickResult(status="idle").status == "idle"
    assert AutonomousSupervisorTickResult(
        status="waiting", **identity, next_wake_event="new_evidence"
    ).status == "waiting"
    assert AutonomousSupervisorTickResult(
        status="completed", **identity
    ).status == "completed"
    assert AutonomousSupervisorTickResult(
        status="blocked", **identity, next_wake_at=NOW + dt.timedelta(minutes=5)
    ).status == "blocked"
    assert AutonomousSupervisorTickResult(status="failed", **identity).status == "failed"
    for status in ("waiting", "completed", "blocked", "failed"):
        with pytest.raises(ValidationError, match="tick_identity_required"):
            AutonomousSupervisorTickResult(status=status)
    with pytest.raises(ValidationError, match="tick_identity_incomplete"):
        AutonomousSupervisorTickResult(status="waiting", task_id=task_id, next_wake_event="event")
    with pytest.raises(ValidationError, match="waiting_result_wake_required"):
        AutonomousSupervisorTickResult(status="waiting", **identity)
    with pytest.raises(ValidationError, match="terminal_result_wake_invalid"):
        AutonomousSupervisorTickResult(status="completed", **identity, next_wake_event="later")
    with pytest.raises(ValidationError, match="idle_result_fields_invalid"):
        AutonomousSupervisorTickResult(status="idle", **identity)
    for field, value in (("model_calls", -1), ("model_calls", 13), ("tool_calls", -1), ("tool_calls", 25)):
        with pytest.raises(ValidationError):
            AutonomousSupervisorTickResult(status="failed", **identity, **{field: value})
