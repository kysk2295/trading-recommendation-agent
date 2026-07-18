from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol, override

from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle
from trading_agent.us_subscription_models import (
    ActiveMarketDataSubscription,
    SubscriptionCooldown,
    SubscriptionPolicyConfig,
    SubscriptionPolicyDecision,
    SubscriptionPolicyStatus,
)


class RuntimePolicyScopeError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime policy scope input is invalid"


@dataclass(frozen=True, slots=True)
class RuntimePolicyScopeRequest:
    evaluated_at: dt.datetime
    active: tuple[ActiveMarketDataSubscription, ...]
    cooldowns: tuple[SubscriptionCooldown, ...]
    policy_config: SubscriptionPolicyConfig


@dataclass(frozen=True, slots=True)
class PreparedRuntimePolicyScope:
    opportunity: OpportunitySnapshot
    decision: SubscriptionPolicyDecision
    completed_minute: int


class ScannerBundleReader(Protocol):
    def latest_bundle(self) -> UsOpportunityScannerBundle | None: ...


def prepare_runtime_policy_scope(
    scanner: ScannerBundleReader,
    request: RuntimePolicyScopeRequest,
) -> PreparedRuntimePolicyScope:
    try:
        if type(request) is not RuntimePolicyScopeRequest or not _aware(request.evaluated_at):
            raise RuntimePolicyScopeError
        bundle = scanner.latest_bundle()
        if type(bundle) is not UsOpportunityScannerBundle:
            raise RuntimePolicyScopeError
        decision = build_subscription_policy_decision(
            bundle.snapshot,
            evaluated_at=request.evaluated_at,
            active=request.active,
            cooldowns=request.cooldowns,
            config=request.policy_config,
        )
        _validate_ready_scope(bundle, decision, request.evaluated_at)
        return PreparedRuntimePolicyScope(
            bundle.opportunity,
            decision,
            completed_regular_minute(request.evaluated_at),
        )
    except (AttributeError, TypeError, ValueError):
        raise RuntimePolicyScopeError from None


def completed_regular_minute(evaluated_at: dt.datetime) -> int:
    try:
        current = evaluated_at.astimezone(NEW_YORK)
        bounds = regular_session_bounds(current.date())
        if bounds is None:
            raise RuntimePolicyScopeError
        boundary = current.replace(second=0, microsecond=0)
        minutes = int((boundary - bounds[0]) / dt.timedelta(minutes=1))
        if minutes <= 0 or boundary >= bounds[1]:
            raise RuntimePolicyScopeError
        return minutes
    except (AttributeError, TypeError, ValueError):
        raise RuntimePolicyScopeError from None


def _validate_ready_scope(
    bundle: UsOpportunityScannerBundle,
    decision: SubscriptionPolicyDecision,
    evaluated_at: dt.datetime,
) -> None:
    desired_symbols = tuple(item.symbol for item in decision.desired)
    opportunity_symbols = tuple(item.symbol for item in bundle.opportunity.candidates)
    snapshot_symbols = tuple(item.symbol for item in bundle.snapshot.candidates)
    if (
        decision.status is not SubscriptionPolicyStatus.READY
        or not decision.desired
        or evaluated_at >= bundle.opportunity.valid_until
        or bundle.opportunity.observed_at != bundle.snapshot.observed_at
        or desired_symbols != opportunity_symbols
        or snapshot_symbols != opportunity_symbols
    ):
        raise RuntimePolicyScopeError


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "PreparedRuntimePolicyScope",
    "RuntimePolicyScopeError",
    "RuntimePolicyScopeRequest",
    "completed_regular_minute",
    "prepare_runtime_policy_scope",
)
