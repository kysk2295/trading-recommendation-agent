from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_http import (
    AlpacaCredentials,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
    load_alpaca_credentials,
)
from trading_agent.alpaca_private_credentials import (
    PrivateAlpacaCredentialsError,
    load_private_alpaca_credentials,
)
from trading_agent.alpaca_sip_dynamic_backoff import AlpacaSipDynamicBackoffConfig
from trading_agent.alpaca_sip_live_actionability import AlpacaSipLiveActionabilityConfig
from trading_agent.alpaca_sip_live_actionability_dependencies import (
    default_alpaca_sip_live_actionability_dependencies,
)
from trading_agent.us_runtime_actionability_plan import RuntimeActionabilityPlanConfig
from trading_agent.us_runtime_live_actionability_dispatch import (
    UsRuntimeLiveActionabilityDispatchRequest,
    UsRuntimeLiveActionabilityDispatchResult,
    dispatch_us_runtime_live_actionability,
)


class RuntimeLiveActionabilityConfigError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime live actionability configuration is invalid"


@dataclass(frozen=True, slots=True)
class RuntimeLiveActionabilityConfig:
    receipt_root: Path | None
    output_store: Path | None
    armed: bool
    actionability: RuntimeActionabilityPlanConfig

    def __post_init__(self) -> None:
        enabled = self.armed or self.receipt_root is not None or self.output_store is not None
        complete = self.armed and self.receipt_root is not None and self.output_store is not None
        upstream = self.actionability.manifest_root is not None and self.actionability.plan_store is not None
        if enabled and (not complete or not upstream):
            raise RuntimeLiveActionabilityConfigError

    def dispatch(
        self,
        evaluated_at: dt.datetime,
        credentials: AlpacaCredentials,
    ) -> UsRuntimeLiveActionabilityDispatchResult | None:
        if not self.armed:
            return None
        manifest_root = self.actionability.manifest_root
        plan_store = self.actionability.plan_store
        if manifest_root is None or plan_store is None or self.receipt_root is None or self.output_store is None:
            raise RuntimeLiveActionabilityConfigError
        return dispatch_us_runtime_live_actionability(
            UsRuntimeLiveActionabilityDispatchRequest(
                manifest_root,
                evaluated_at,
                credentials,
                plan_store,
                self.actionability.policy_state_store,
                self.receipt_root,
                self.output_store,
                AlpacaSipLiveActionabilityConfig(
                    1,
                    AlpacaSipDynamicBackoffConfig(1.0, 2.0, 4.0),
                    10,
                    5.0,
                ),
            ),
            default_alpaca_sip_live_actionability_dependencies(),
        )

    def load_credentials(self, path: Path) -> AlpacaCredentials:
        try:
            if self.armed:
                return load_private_alpaca_credentials(path)
            return load_alpaca_credentials(path)
        except (
            AlpacaSecretFileError,
            MissingAlpacaCredentialsError,
            OSError,
            PrivateAlpacaCredentialsError,
        ):
            raise RuntimeLiveActionabilityConfigError from None


def live_actionability_report_detail(
    result: UsRuntimeLiveActionabilityDispatchResult | None,
) -> str:
    if result is None:
        return "live actionability: disabled"
    return (
        f"live actionability: {result.selected_count} selected, "
        f"{result.created_count} new, {result.replay_count} replay"
    )


__all__ = (
    "RuntimeLiveActionabilityConfig",
    "RuntimeLiveActionabilityConfigError",
    "live_actionability_report_detail",
)
