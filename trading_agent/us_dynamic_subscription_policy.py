"""Pure broad-candidate to bounded US quote/trade subscription policy."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final

from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_subscription_models import (
    ActiveMarketDataSubscription,
    BroadScannerCandidate,
    BroadScannerSnapshot,
    DesiredMarketDataSubscription,
    SubscriptionAction,
    SubscriptionActionKind,
    SubscriptionChannel,
    SubscriptionCooldown,
    SubscriptionPolicyConfig,
    SubscriptionPolicyDecision,
    SubscriptionPolicyError,
    SubscriptionPolicyStatus,
)
from trading_agent.us_subscription_validation import validate_subscription_policy_inputs

_POLICY_SEMANTIC_VERSION: Final = "us_dynamic_quote_trade_v1"


def build_subscription_policy_decision(
    snapshot: BroadScannerSnapshot,
    *,
    evaluated_at: dt.datetime,
    active: tuple[ActiveMarketDataSubscription, ...],
    cooldowns: tuple[SubscriptionCooldown, ...],
    config: SubscriptionPolicyConfig,
) -> SubscriptionPolicyDecision:
    validate_subscription_policy_inputs(snapshot, evaluated_at, active, cooldowns, config)
    channels = (SubscriptionChannel.QUOTE, SubscriptionChannel.TRADE)
    if not _in_regular_session(evaluated_at) or not _in_regular_session(snapshot.observed_at):
        return _blocked_decision(
            snapshot,
            evaluated_at,
            active,
            config,
            SubscriptionPolicyStatus.BLOCKED_SESSION_CLOSED,
            channels,
        )
    if evaluated_at - snapshot.observed_at > config.max_candidate_age:
        return _blocked_decision(
            snapshot,
            evaluated_at,
            active,
            config,
            SubscriptionPolicyStatus.BLOCKED_STALE,
            channels,
        )

    ranked = tuple(sorted(snapshot.candidates, key=_candidate_rank_key))
    active_by_id = {item.instrument_id: item for item in active}
    cooldown_by_id = {item.instrument_id: item for item in cooldowns}
    eligible = tuple(
        candidate
        for candidate in ranked
        if candidate.instrument_id not in cooldown_by_id
        or cooldown_by_id[candidate.instrument_id].eligible_after <= evaluated_at
    )
    protected = tuple(
        candidate
        for candidate in eligible
        if candidate.instrument_id in active_by_id
        and evaluated_at - active_by_id[candidate.instrument_id].subscribed_at
        < config.minimum_residency
    )[: config.capacity]
    protected_ids = {candidate.instrument_id for candidate in protected}
    remaining = config.capacity - len(protected)
    competitors = tuple(
        candidate for candidate in eligible if candidate.instrument_id not in protected_ids
    )
    selected_ids = protected_ids | {
        candidate.instrument_id for candidate in competitors[:remaining]
    }
    selected = tuple(candidate for candidate in eligible if candidate.instrument_id in selected_ids)
    desired = tuple(
        DesiredMarketDataSubscription(candidate.instrument_id, candidate.symbol, channels)
        for candidate in selected
    )
    removed = tuple(
        item for item in sorted(active, key=lambda item: item.instrument_id)
        if item.instrument_id not in selected_ids
    )
    added = tuple(item for item in desired if item.instrument_id not in active_by_id)
    actions = (
        *(
            SubscriptionAction(
                SubscriptionActionKind.UNSUBSCRIBE,
                item.instrument_id,
                item.symbol,
                channels,
            )
            for item in removed
        ),
        *(
            SubscriptionAction(
                SubscriptionActionKind.SUBSCRIBE,
                item.instrument_id,
                item.symbol,
                channels,
            )
            for item in added
        ),
    )
    new_cooldowns = tuple(
        SubscriptionCooldown(
            instrument_id=item.instrument_id,
            symbol=item.symbol,
            eligible_after=evaluated_at + config.eviction_cooldown,
        )
        for item in removed
    )
    return SubscriptionPolicyDecision(
        identity=snapshot.identity,
        evaluated_at=evaluated_at,
        candidate_observed_at=snapshot.observed_at,
        status=SubscriptionPolicyStatus.READY,
        policy_semantic_version=_POLICY_SEMANTIC_VERSION,
        config=config,
        desired=desired,
        actions=actions,
        new_cooldowns=new_cooldowns,
    )


def _blocked_decision(
    snapshot: BroadScannerSnapshot,
    evaluated_at: dt.datetime,
    active: tuple[ActiveMarketDataSubscription, ...],
    config: SubscriptionPolicyConfig,
    status: SubscriptionPolicyStatus,
    channels: tuple[SubscriptionChannel, ...],
) -> SubscriptionPolicyDecision:
    actions = tuple(
        SubscriptionAction(
            SubscriptionActionKind.UNSUBSCRIBE,
            item.instrument_id,
            item.symbol,
            channels,
        )
        for item in sorted(active, key=lambda item: item.instrument_id)
    )
    return SubscriptionPolicyDecision(
        identity=snapshot.identity,
        evaluated_at=evaluated_at,
        candidate_observed_at=snapshot.observed_at,
        status=status,
        policy_semantic_version=_POLICY_SEMANTIC_VERSION,
        config=config,
        desired=(),
        actions=actions,
        new_cooldowns=(),
    )


def _candidate_rank_key(candidate: BroadScannerCandidate) -> tuple[Decimal, int, str, str]:
    return (-candidate.priority_score, candidate.source_rank, candidate.instrument_id, candidate.symbol)


def _in_regular_session(value: dt.datetime) -> bool:
    current = value.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    return bounds is not None and bounds[0] <= current < bounds[1]


__all__ = (
    "ActiveMarketDataSubscription",
    "BroadScannerCandidate",
    "BroadScannerSnapshot",
    "DesiredMarketDataSubscription",
    "SubscriptionAction",
    "SubscriptionActionKind",
    "SubscriptionChannel",
    "SubscriptionCooldown",
    "SubscriptionPolicyConfig",
    "SubscriptionPolicyDecision",
    "SubscriptionPolicyError",
    "SubscriptionPolicyStatus",
    "build_subscription_policy_decision",
)
