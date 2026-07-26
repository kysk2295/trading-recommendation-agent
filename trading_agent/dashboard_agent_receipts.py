from __future__ import annotations

import datetime as dt
import hashlib
from typing import Literal

from trading_agent.dashboard_autonomous_research import (
    AutonomousTaskReceiptV1,
    AutonomousTriggerV1,
    TaskState,
)
from trading_agent.dashboard_outbound_redaction import redact_outbound_text, require_safe_outbound_text

ReceiptKind = Literal["blocker", "claim", "progress", "evidence", "result", "cleanup"]


def task_id_for(trigger: AutonomousTriggerV1) -> str:
    key = f"{trigger.agent_family_id}:{trigger.policy_version}:{trigger.dedupe_key}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def build_receipt(
    trigger: AutonomousTriggerV1,
    task_id: str,
    sequence: int,
    kind: ReceiptKind,
    state: TaskState,
    occurred_at: dt.datetime,
    *,
    reason: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    result_sha256: str | None = None,
    summary: str | None = None,
    consumed_tokens: int = 0,
    consumed_cost: int = 0,
) -> AutonomousTaskReceiptV1:
    safe_summary = None if summary is None else redact_outbound_text(summary)
    if safe_summary is not None:
        require_safe_outbound_text(safe_summary)
    event_id = hashlib.sha256(f"{task_id}:{sequence}:{kind}:{state}:{reason}".encode()).hexdigest()
    return AutonomousTaskReceiptV1(
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


__all__ = ("build_receipt", "task_id_for")
