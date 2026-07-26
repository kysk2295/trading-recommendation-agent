from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from trading_agent.dashboard_agent_store import AutonomousTaskStore
from trading_agent.dashboard_autonomous_research import (
    AutonomousTaskReceiptV1,
    AutonomousTriggerV1,
    TaskState,
)
from trading_agent.dashboard_outbound_redaction import (
    redact_outbound_text,
    require_safe_outbound_text,
)
from trading_agent.dashboard_worktree_executor import AutonomousTaskExecutor

OutcomeState = Literal["completed", "failed", "uncertain", "blocked", "duplicate"]


class AutonomousEventSink(Protocol):
    def __call__(self, receipt: AutonomousTaskReceiptV1, /) -> None: ...


@dataclass(frozen=True, slots=True)
class AutonomousPolicy:
    max_trigger_age_seconds: int
    max_daily_tokens_per_family: int
    max_daily_cost_microusd_per_family: int
    cooldown_seconds: int
    max_global_concurrency: int
    max_family_concurrency: int
    rolling_failure_window_seconds: int
    max_rolling_failures: int

    @classmethod
    def permissive_for_tests(cls) -> AutonomousPolicy:
        return cls(
            max_trigger_age_seconds=3_600,
            max_daily_tokens_per_family=1_000_000,
            max_daily_cost_microusd_per_family=100_000_000,
            cooldown_seconds=0,
            max_global_concurrency=8,
            max_family_concurrency=2,
            rolling_failure_window_seconds=3_600,
            max_rolling_failures=8,
        )


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
        event_sink: AutonomousEventSink | None = None,
    ) -> None:
        self._store = AutonomousTaskStore(state_root)
        self._executor = executor
        self._policy = policy
        self._event_sink = event_sink

    def handle(
        self,
        trigger: AutonomousTriggerV1,
        *,
        now: dt.datetime | None = None,
    ) -> AutonomousOutcome:
        occurred_at = dt.datetime.now(dt.UTC) if now is None else now
        blocker = self._blocker(trigger, occurred_at)
        if blocker is not None:
            task_id = self._task_id(trigger)
            self._append(
                trigger,
                task_id,
                0,
                "blocker",
                "blocked",
                trigger.authorized_at,
                reason=blocker,
            )
            return AutonomousOutcome(task_id, "blocked", blocker, False, 0, False)
        task_id, created = self._store.claim(trigger)
        if not created:
            return AutonomousOutcome(task_id, "duplicate", "duplicate_claim", False, 0, False)
        self._append(
            trigger,
            task_id,
            0,
            "claim",
            "claimed",
            occurred_at,
            consumed_tokens=trigger.budget_envelope.max_tokens,
            consumed_cost=trigger.budget_envelope.max_cost_microusd,
        )
        self._append(trigger, task_id, 1, "progress", "running", occurred_at)
        result = self._executor.execute(trigger, task_id)
        terminal: TaskState = result.state
        if result.evidence_sha256:
            self._append(
                trigger,
                task_id,
                2,
                "evidence",
                terminal,
                occurred_at,
                evidence_refs=result.evidence_sha256,
            )
        result_sequence = 3 if result.evidence_sha256 else 2
        self._append(
            trigger,
            task_id,
            result_sequence,
            "result",
            terminal,
            occurred_at,
            reason=None if terminal == "completed" else "autonomous_execution_failed",
            result_sha256=result.result_sha256,
            summary=result.result_summary,
        )
        self._append(
            trigger,
            task_id,
            result_sequence + 1,
            "cleanup",
            terminal if result.cleanup_completed else "uncertain",
            occurred_at,
            reason=None if result.cleanup_completed else "cleanup_unconfirmed",
        )
        outcome_state: OutcomeState = result.state if result.cleanup_completed else "uncertain"
        return AutonomousOutcome(
            task_id,
            outcome_state,
            None if outcome_state == "completed" else "autonomous_execution_failed",
            True,
            int(result.process_started),
            result.cleanup_completed,
        )

    def _blocker(self, trigger: AutonomousTriggerV1, now: dt.datetime) -> str | None:
        if now < trigger.authorized_at:
            return "authorization_not_current"
        if (
            now > trigger.expires_at
            or (now - trigger.observed_at).total_seconds() > self._policy.max_trigger_age_seconds
        ):
            return "trigger_stale"
        receipts = self._store.receipts()
        claims = tuple(item for item in receipts if item.kind == "claim")
        family_claims = tuple(item for item in claims if item.agent_family_id == trigger.agent_family_id)
        day_claims = tuple(item for item in family_claims if item.occurred_at.date() == now.date())
        if sum(item.consumed_tokens for item in day_claims) + trigger.budget_envelope.max_tokens > (
            self._policy.max_daily_tokens_per_family
        ):
            return "family_token_budget_exhausted"
        if sum(item.consumed_cost_microusd for item in day_claims) + trigger.budget_envelope.max_cost_microusd > (
            self._policy.max_daily_cost_microusd_per_family
        ):
            return "family_cost_budget_exhausted"
        if family_claims and (now - max(item.occurred_at for item in family_claims)).total_seconds() < (
            self._policy.cooldown_seconds
        ):
            return "family_cooldown_active"
        latest = _latest_by_task(receipts)
        active = tuple(item for item in latest if item.state in {"claimed", "running"})
        if len(active) >= self._policy.max_global_concurrency:
            return "global_concurrency_exhausted"
        if sum(item.agent_family_id == trigger.agent_family_id for item in active) >= (
            self._policy.max_family_concurrency
        ):
            return "family_concurrency_exhausted"
        failure_floor = now - dt.timedelta(seconds=self._policy.rolling_failure_window_seconds)
        failures = tuple(
            item for item in latest if item.state in {"failed", "uncertain"} and item.occurred_at >= failure_floor
        )
        if len(failures) >= self._policy.max_rolling_failures:
            return "rolling_failure_budget_exhausted"
        return None

    def _append(
        self,
        trigger: AutonomousTriggerV1,
        task_id: str,
        sequence: int,
        kind: Literal["blocker", "claim", "progress", "evidence", "result", "cleanup"],
        state: TaskState,
        occurred_at: dt.datetime,
        *,
        reason: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        result_sha256: str | None = None,
        summary: str | None = None,
        consumed_tokens: int = 0,
        consumed_cost: int = 0,
    ) -> None:
        safe_summary = None if summary is None else redact_outbound_text(summary)
        if safe_summary is not None:
            require_safe_outbound_text(safe_summary)
        event_id = hashlib.sha256(f"{task_id}:{sequence}:{kind}:{state}:{reason}".encode()).hexdigest()
        receipt = AutonomousTaskReceiptV1(
            public_task_id=task_id,
            event_id=event_id,
            agent_family_id=trigger.agent_family_id,
            trigger_type=trigger.trigger_type,
            policy_version=trigger.policy_version,
            code_version=trigger.environment_spec.pinned_code_sha,
            sequence=sequence,
            kind=kind,
            state=state,
            occurred_at=occurred_at,
            reason=reason,
            evidence_refs=evidence_refs,
            result_sha256=result_sha256,
            summary=safe_summary,
            consumed_tokens=consumed_tokens,
            consumed_cost_microusd=consumed_cost,
        )
        created = self._store.append(receipt)
        if created and self._event_sink is not None:
            self._event_sink(receipt)

    @staticmethod
    def _task_id(trigger: AutonomousTriggerV1) -> str:
        key = f"{trigger.agent_family_id}:{trigger.policy_version}:{trigger.dedupe_key}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]


def _latest_by_task(
    receipts: tuple[AutonomousTaskReceiptV1, ...],
) -> tuple[AutonomousTaskReceiptV1, ...]:
    latest: dict[str, AutonomousTaskReceiptV1] = {}
    for receipt in receipts:
        current = latest.get(receipt.public_task_id)
        if current is None or receipt.sequence > current.sequence:
            latest[receipt.public_task_id] = receipt
    return tuple(latest.values())


__all__ = (
    "AutonomousControlPlane",
    "AutonomousEventSink",
    "AutonomousOutcome",
    "AutonomousPolicy",
)
