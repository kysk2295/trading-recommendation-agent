from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_sip_dynamic_plan_store import (
    AlpacaSipDynamicPlanRollResult,
    AlpacaSipDynamicPlanStore,
)
from trading_agent.us_subscription_policy_state import SubscriptionPolicyRuntimeState


class RuntimeActionabilityPlanConfigError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime actionability plan configuration is invalid"


@dataclass(frozen=True, slots=True)
class RuntimeActionabilityPlanConfig:
    signal_outbox: Path | None
    manifest_root: Path | None
    explicit_plan_store: Path | None
    policy_state_store: Path

    def __post_init__(self) -> None:
        signal_enabled = self.signal_outbox is not None
        manifest_enabled = self.manifest_root is not None
        if signal_enabled != manifest_enabled or (not signal_enabled and self.explicit_plan_store is not None):
            raise RuntimeActionabilityPlanConfigError

    @property
    def plan_store(self) -> Path | None:
        if self.signal_outbox is None:
            return None
        if self.explicit_plan_store is not None:
            return self.explicit_plan_store
        name = f"{self.policy_state_store.stem}.dynamic-plans.sqlite3"
        return self.policy_state_store.with_name(name)

    def roll(self, state: SubscriptionPolicyRuntimeState) -> AlpacaSipDynamicPlanRollResult | None:
        path = self.plan_store
        if path is None:
            return None
        return AlpacaSipDynamicPlanStore(path).roll(state)


def dynamic_plan_report_detail(result: AlpacaSipDynamicPlanRollResult | None) -> str:
    if result is None:
        return "dynamic plan: disabled"
    return f"dynamic plan: {'new' if result.appended else 'replay'}"


__all__ = (
    "RuntimeActionabilityPlanConfig",
    "RuntimeActionabilityPlanConfigError",
    "dynamic_plan_report_detail",
)
