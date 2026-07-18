from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, final, override

from trading_agent.intraday_feature_kernel import FeatureSnapshotStatus
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_feature_evidence_models import UsFeatureEvidenceBinding
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeResult,
    MarketDataRuntimeStatus,
    RuntimeFeatureRequest,
    validate_runtime_request_for_evaluation,
)
from trading_agent.us_subscription_models import (
    DesiredMarketDataSubscription,
    SubscriptionPolicyDecision,
    SubscriptionPolicyStatus,
)


class UsMarketDataFleetError(ValueError):
    @override
    def __str__(self) -> str:
        return "market data fleet input is invalid"


class RuntimeOwnerBlockedError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "market data runtime owner is blocked"


class RuntimeFleetStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED_POLICY = "blocked_policy"


class RuntimeOwnerStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RuntimeOwnerOutcome:
    subscription: DesiredMarketDataSubscription
    status: RuntimeOwnerStatus
    runtime_result: MarketDataRuntimeResult | None


@dataclass(frozen=True, slots=True)
class RuntimeFleetResult:
    status: RuntimeFleetStatus
    identity: ResearchInputIdentity
    evaluated_at: dt.datetime
    outcomes: tuple[RuntimeOwnerOutcome, ...]
    bindings: tuple[UsFeatureEvidenceBinding, ...]


class UsMarketDataRuntimeOwner(Protocol):
    @property
    def instrument_id(self) -> str: ...

    @property
    def symbol(self) -> str: ...

    def run_cycle(
        self,
        decision: SubscriptionPolicyDecision,
        request: RuntimeFeatureRequest,
    ) -> MarketDataRuntimeResult: ...


class UsMarketDataRuntimeOwnerFactory(Protocol):
    def create(
        self,
        subscription: DesiredMarketDataSubscription,
    ) -> UsMarketDataRuntimeOwner: ...


@final
class UsMarketDataFleet:
    __slots__ = ("_factory", "_owners")

    def __init__(self, factory: UsMarketDataRuntimeOwnerFactory) -> None:
        self._factory = factory
        self._owners: dict[str, UsMarketDataRuntimeOwner] = {}

    @property
    def active_instrument_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._owners))

    def run_cycle(
        self,
        decision: SubscriptionPolicyDecision,
        requests: tuple[RuntimeFeatureRequest, ...],
    ) -> RuntimeFleetResult:
        request_by_id = _validate_cycle(decision, requests)
        if decision.status is not SubscriptionPolicyStatus.READY or not decision.desired:
            self._owners.clear()
            return RuntimeFleetResult(
                RuntimeFleetStatus.BLOCKED_POLICY,
                decision.identity,
                decision.evaluated_at,
                (),
                (),
            )
        desired_ids = {item.instrument_id for item in decision.desired}
        self._owners = {
            instrument_id: owner for instrument_id, owner in self._owners.items() if instrument_id in desired_ids
        }
        outcomes: list[RuntimeOwnerOutcome] = []
        bindings: list[UsFeatureEvidenceBinding] = []
        for subscription in decision.desired:
            try:
                owner = self._owner(subscription)
                runtime_result = owner.run_cycle(
                    decision,
                    request_by_id[subscription.instrument_id],
                )
            except RuntimeOwnerBlockedError:
                outcomes.append(RuntimeOwnerOutcome(subscription, RuntimeOwnerStatus.FAILED, None))
                continue
            outcome, binding = _runtime_outcome(subscription, runtime_result)
            outcomes.append(outcome)
            if binding is not None:
                bindings.append(binding)
        status = RuntimeFleetStatus.READY if len(bindings) == len(decision.desired) else RuntimeFleetStatus.DEGRADED
        return RuntimeFleetResult(
            status,
            decision.identity,
            decision.evaluated_at,
            tuple(outcomes),
            tuple(bindings),
        )

    def _owner(
        self,
        subscription: DesiredMarketDataSubscription,
    ) -> UsMarketDataRuntimeOwner:
        owner = self._owners.get(subscription.instrument_id)
        if owner is None:
            owner = self._factory.create(subscription)
            self._owners[subscription.instrument_id] = owner
        if owner.instrument_id != subscription.instrument_id or owner.symbol != subscription.symbol:
            raise UsMarketDataFleetError
        return owner


def _validate_cycle(
    decision: SubscriptionPolicyDecision,
    requests: tuple[RuntimeFeatureRequest, ...],
) -> dict[str, RuntimeFeatureRequest]:
    if (
        type(decision) is not SubscriptionPolicyDecision
        or type(decision.identity) is not ResearchInputIdentity
        or type(requests) is not tuple
        or len(decision.desired) > decision.config.capacity
        or any(type(subscription) is not DesiredMarketDataSubscription for subscription in decision.desired)
    ):
        raise UsMarketDataFleetError
    desired_ids = tuple(item.instrument_id for item in decision.desired)
    desired_symbols = tuple(item.symbol for item in decision.desired)
    if len(desired_ids) != len(set(desired_ids)) or len(desired_symbols) != len(set(desired_symbols)):
        raise UsMarketDataFleetError
    request_by_id: dict[str, RuntimeFeatureRequest] = {}
    for request in requests:
        try:
            validate_runtime_request_for_evaluation(request, decision.evaluated_at)
        except ValueError:
            raise UsMarketDataFleetError from None
        request_by_id[request.instrument_id] = request
    if len(request_by_id) != len(requests) or set(request_by_id) != set(desired_ids):
        raise UsMarketDataFleetError
    return request_by_id


def _runtime_outcome(
    subscription: DesiredMarketDataSubscription,
    result: MarketDataRuntimeResult,
) -> tuple[RuntimeOwnerOutcome, UsFeatureEvidenceBinding | None]:
    if type(result) is not MarketDataRuntimeResult:
        raise UsMarketDataFleetError
    if result.status is not MarketDataRuntimeStatus.READY:
        if result.feature_snapshots:
            raise UsMarketDataFleetError
        return RuntimeOwnerOutcome(subscription, RuntimeOwnerStatus.BLOCKED, result), None
    if len(result.feature_snapshots) != 1:
        raise UsMarketDataFleetError
    snapshot = result.feature_snapshots[0]
    if snapshot.instrument_id != subscription.instrument_id:
        raise UsMarketDataFleetError
    if snapshot.status is not FeatureSnapshotStatus.READY:
        return RuntimeOwnerOutcome(subscription, RuntimeOwnerStatus.BLOCKED, result), None
    return (
        RuntimeOwnerOutcome(subscription, RuntimeOwnerStatus.READY, result),
        UsFeatureEvidenceBinding(subscription.symbol, snapshot),
    )


__all__ = (
    "RuntimeFleetResult",
    "RuntimeFleetStatus",
    "RuntimeOwnerBlockedError",
    "RuntimeOwnerOutcome",
    "RuntimeOwnerStatus",
    "UsMarketDataFleet",
    "UsMarketDataFleetError",
    "UsMarketDataRuntimeOwner",
    "UsMarketDataRuntimeOwnerFactory",
)
