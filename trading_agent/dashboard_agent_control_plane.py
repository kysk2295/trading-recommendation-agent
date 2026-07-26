from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from trading_agent.dashboard_agent_admission import AutonomousPolicy
from trading_agent.dashboard_agent_receipts import build_receipt
from trading_agent.dashboard_agent_store import AutonomousTaskStore, InvalidAutonomousTaskStoreError
from trading_agent.dashboard_autonomous_research import AutonomousTaskReceiptV1, AutonomousTriggerV1
from trading_agent.dashboard_trigger_authority import TriggerAuthorityResolver
from trading_agent.dashboard_worktree_executor import AutonomousTaskExecutor

OutcomeState = Literal["completed", "failed", "uncertain", "blocked"]
FaultSeam = Literal[
    "authorization",
    "claim",
    "process_launch",
    "tool_step",
    "result_persistence",
    "event_send",
    "cleanup",
]


class AutonomousEventSink(Protocol):
    def __call__(self, receipt: AutonomousTaskReceiptV1, /) -> None: ...


@dataclass(frozen=True, slots=True)
class InjectedAutonomousFault(RuntimeError):
    seam: FaultSeam


@dataclass(frozen=True, slots=True)
class AutonomousOutcome:
    public_task_id: str
    state: OutcomeState
    reason: str | None
    claim_created: bool
    model_processes: int
    cleanup_completed: bool


class AutonomousControlPlane:
    def __init__(
        self,
        *,
        state_root: Path,
        executor: AutonomousTaskExecutor,
        policy: AutonomousPolicy,
        authority_resolver: TriggerAuthorityResolver,
        event_sink: AutonomousEventSink | None = None,
        fault_seam: FaultSeam | None = None,
    ) -> None:
        self._store = AutonomousTaskStore(state_root)
        self._executor = executor
        self._policy = policy
        self._authority = authority_resolver
        self._event_sink = event_sink
        self._fault_seam = fault_seam

    def handle(
        self,
        trigger: AutonomousTriggerV1,
        *,
        now: dt.datetime | None = None,
    ) -> AutonomousOutcome:
        occurred_at = dt.datetime.now(dt.UTC) if now is None else now
        authorization = self._authorization_blocker(trigger, occurred_at)
        if authorization is not None:
            return self._blocked(trigger, authorization)
        preflight = self._executor.preflight(trigger)
        if preflight is not None:
            return self._blocked(trigger, preflight)
        try:
            self._trip("claim")
        except InjectedAutonomousFault:
            return self._blocked(trigger, "claim_fault")
        decision = self._store.admit(trigger, occurred_at, self._policy)
        if decision.kind == "blocked":
            self._emit(decision.receipt, faultable=False)
            return AutonomousOutcome(decision.task_id, "blocked", decision.reason, False, 0, False)
        if decision.kind == "duplicate":
            state = "uncertain" if decision.replay_state is None else decision.replay_state
            return AutonomousOutcome(decision.task_id, state, decision.reason, False, 0, state == "completed")
        task_id = decision.task_id
        try:
            self._emit(decision.receipt, faultable=True)
            self._append(trigger, task_id, 1, "progress", "running", occurred_at)
            self._trip("process_launch")
            self._trip("tool_step")
            result = self._executor.execute(trigger, task_id)
            sequence = 2
            if result.evidence_sha256:
                self._append(
                    trigger,
                    task_id,
                    sequence,
                    "evidence",
                    result.state,
                    occurred_at,
                    evidence_refs=result.evidence_sha256,
                )
                sequence += 1
            self._trip("result_persistence")
            self._append(
                trigger,
                task_id,
                sequence,
                "result",
                result.state,
                occurred_at,
                reason=None if result.state == "completed" else "autonomous_execution_failed",
                result_sha256=result.result_sha256,
                summary=result.result_summary,
            )
            self._trip("cleanup")
            cleanup_state = result.state if result.cleanup_completed else "uncertain"
            self._append(
                trigger,
                task_id,
                sequence + 1,
                "cleanup",
                cleanup_state,
                occurred_at,
                reason=None if result.cleanup_completed else "cleanup_unconfirmed",
            )
        except InjectedAutonomousFault as error:
            return self._terminalize_fault(trigger, task_id, occurred_at, error.seam)
        except (InvalidAutonomousTaskStoreError, OSError, RuntimeError):
            return self._terminalize_fault(trigger, task_id, occurred_at, "process_launch")
        outcome_state: OutcomeState = result.state if result.cleanup_completed else "uncertain"
        return AutonomousOutcome(
            task_id,
            outcome_state,
            None if outcome_state == "completed" else "autonomous_execution_failed",
            True,
            int(result.process_started),
            result.cleanup_completed,
        )

    def _authorization_blocker(self, trigger: AutonomousTriggerV1, now: dt.datetime) -> str | None:
        try:
            self._trip("authorization")
        except InjectedAutonomousFault:
            return "authorization_fault"
        return self._authority.blocker(trigger, now)

    def _blocked(self, trigger: AutonomousTriggerV1, reason: str) -> AutonomousOutcome:
        decision = self._store.reject(trigger, reason)
        self._emit(decision.receipt, faultable=False)
        state: OutcomeState = "blocked" if decision.replay_state is None else decision.replay_state
        return AutonomousOutcome(decision.task_id, state, reason, False, 0, False)

    def _terminalize_fault(
        self,
        trigger: AutonomousTriggerV1,
        task_id: str,
        occurred_at: dt.datetime,
        seam: FaultSeam,
    ) -> AutonomousOutcome:
        state: Literal["failed", "uncertain"] = (
            "failed" if seam in {"process_launch", "tool_step"} else "uncertain"
        )
        receipts = tuple(item for item in self._store.receipts() if item.public_task_id == task_id)
        sequence = 1 + max(item.sequence for item in receipts)
        receipt = build_receipt(
            trigger,
            task_id,
            sequence,
            "result",
            state,
            occurred_at,
            reason=f"{seam}_fault",
        )
        _ = self._store.append(receipt)
        return AutonomousOutcome(task_id, state, f"{seam}_fault", True, 0, False)

    def _append(
        self,
        trigger: AutonomousTriggerV1,
        task_id: str,
        sequence: int,
        kind: Literal["progress", "evidence", "result", "cleanup"],
        state: Literal["running", "completed", "failed", "uncertain"],
        occurred_at: dt.datetime,
        *,
        reason: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        result_sha256: str | None = None,
        summary: str | None = None,
    ) -> None:
        receipt = build_receipt(
            trigger,
            task_id,
            sequence,
            kind,
            state,
            occurred_at,
            reason=reason,
            evidence_refs=evidence_refs,
            result_sha256=result_sha256,
            summary=summary,
        )
        if self._store.append(receipt):
            self._emit(receipt, faultable=True)

    def _emit(self, receipt: AutonomousTaskReceiptV1 | None, *, faultable: bool) -> None:
        if receipt is None or self._event_sink is None:
            if faultable:
                self._trip("event_send")
            return
        if faultable:
            self._trip("event_send")
        self._event_sink(receipt)

    def _trip(self, seam: FaultSeam) -> None:
        if self._fault_seam == seam:
            raise InjectedAutonomousFault(seam)


__all__ = (
    "AutonomousControlPlane",
    "AutonomousEventSink",
    "AutonomousOutcome",
    "AutonomousPolicy",
    "FaultSeam",
)
