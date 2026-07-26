from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from trading_agent.dashboard_autonomous_research import AutonomousTaskReceiptV1, AutonomousTriggerV1

AdmissionKind = Literal["admitted", "blocked", "duplicate"]
ReplayState = Literal["completed", "failed", "uncertain", "blocked"]


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
        return cls(3_600, 1_000_000, 100_000_000, 0, 8, 2, 3_600, 8)


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    kind: AdmissionKind
    task_id: str
    reason: str | None
    receipt: AutonomousTaskReceiptV1 | None
    replay_state: ReplayState | None


def policy_blocker(
    trigger: AutonomousTriggerV1,
    now: dt.datetime,
    policy: AutonomousPolicy,
    receipts: tuple[AutonomousTaskReceiptV1, ...],
) -> str | None:
    if now < trigger.authorized_at:
        return "authorization_not_current"
    if now > trigger.expires_at or (now - trigger.observed_at).total_seconds() > policy.max_trigger_age_seconds:
        return "trigger_stale"
    claims = tuple(item for item in receipts if item.kind == "claim")
    family_claims = tuple(item for item in claims if item.agent_family_id == trigger.agent_family_id)
    day_claims = tuple(item for item in family_claims if item.occurred_at.date() == now.date())
    if sum(item.consumed_tokens for item in day_claims) + trigger.budget_envelope.max_tokens > (
        policy.max_daily_tokens_per_family
    ):
        return "family_token_budget_exhausted"
    if sum(item.consumed_cost_microusd for item in day_claims) + trigger.budget_envelope.max_cost_microusd > (
        policy.max_daily_cost_microusd_per_family
    ):
        return "family_cost_budget_exhausted"
    if family_claims and (now - max(item.occurred_at for item in family_claims)).total_seconds() < (
        policy.cooldown_seconds
    ):
        return "family_cooldown_active"
    latest = latest_by_task(receipts)
    active = tuple(item for item in latest if item.state in {"claimed", "running"})
    if len(active) >= policy.max_global_concurrency:
        return "global_concurrency_exhausted"
    if sum(item.agent_family_id == trigger.agent_family_id for item in active) >= policy.max_family_concurrency:
        return "family_concurrency_exhausted"
    floor = now - dt.timedelta(seconds=policy.rolling_failure_window_seconds)
    failures = tuple(item for item in latest if item.state in {"failed", "uncertain"} and item.occurred_at >= floor)
    if len(failures) >= policy.max_rolling_failures:
        return "rolling_failure_budget_exhausted"
    return None


def latest_by_task(
    receipts: tuple[AutonomousTaskReceiptV1, ...],
) -> tuple[AutonomousTaskReceiptV1, ...]:
    latest: dict[str, AutonomousTaskReceiptV1] = {}
    for receipt in receipts:
        current = latest.get(receipt.public_task_id)
        if current is None or receipt.sequence > current.sequence:
            latest[receipt.public_task_id] = receipt
    return tuple(latest.values())


__all__ = ("AdmissionDecision", "AutonomousPolicy", "policy_blocker")
