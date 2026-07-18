"""Fail-closed validation for pure US subscription-policy inputs."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Final

from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_subscription_models import (
    ActiveMarketDataSubscription,
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionCooldown,
    SubscriptionPolicyConfig,
    SubscriptionPolicyError,
)

_INSTRUMENT_ID: Final = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SYMBOL: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


def validate_subscription_policy_inputs(
    snapshot: BroadScannerSnapshot,
    evaluated_at: dt.datetime,
    active: tuple[ActiveMarketDataSubscription, ...],
    cooldowns: tuple[SubscriptionCooldown, ...],
    config: SubscriptionPolicyConfig,
) -> None:
    if (
        type(snapshot) is not BroadScannerSnapshot
        or type(snapshot.identity) is not ResearchInputIdentity
        or type(config) is not SubscriptionPolicyConfig
        or not _aware(evaluated_at)
        or not _aware(snapshot.observed_at)
        or snapshot.observed_at > evaluated_at
        or type(snapshot.candidates) is not tuple
        or type(active) is not tuple
        or type(cooldowns) is not tuple
        or type(config.capacity) is not int
        or config.capacity <= 0
        or not _positive_duration(config.max_candidate_age)
        or not _positive_duration(config.minimum_residency)
        or not _positive_duration(config.eviction_cooldown)
    ):
        raise SubscriptionPolicyError
    if not _valid_candidates(snapshot.candidates):
        raise SubscriptionPolicyError
    if not _valid_active(active, evaluated_at) or not _valid_cooldowns(cooldowns):
        raise SubscriptionPolicyError
    active_ids = {item.instrument_id for item in active}
    cooldown_ids = {item.instrument_id for item in cooldowns}
    if active_ids & cooldown_ids or not _consistent_symbols(snapshot, active, cooldowns):
        raise SubscriptionPolicyError


def _valid_candidates(candidates: tuple[BroadScannerCandidate, ...]) -> bool:
    if any(
        type(item) is not BroadScannerCandidate
        or not _valid_identity(item.instrument_id, item.symbol)
        or type(item.priority_score) is not Decimal
        or not item.priority_score.is_finite()
        or item.priority_score < 0
        or type(item.source_rank) is not int
        or item.source_rank <= 0
        for item in candidates
    ):
        return False
    return _unique_identities(tuple((item.instrument_id, item.symbol) for item in candidates))


def _valid_active(
    active: tuple[ActiveMarketDataSubscription, ...],
    evaluated_at: dt.datetime,
) -> bool:
    return not any(
        type(item) is not ActiveMarketDataSubscription
        or not _valid_identity(item.instrument_id, item.symbol)
        or not _aware(item.subscribed_at)
        or item.subscribed_at > evaluated_at
        for item in active
    ) and _unique_identities(tuple((item.instrument_id, item.symbol) for item in active))


def _valid_cooldowns(cooldowns: tuple[SubscriptionCooldown, ...]) -> bool:
    return not any(
        type(item) is not SubscriptionCooldown
        or not _valid_identity(item.instrument_id, item.symbol)
        or not _aware(item.eligible_after)
        for item in cooldowns
    ) and _unique_identities(tuple((item.instrument_id, item.symbol) for item in cooldowns))


def _unique_identities(values: tuple[tuple[str, str], ...]) -> bool:
    return len(values) == len({item[0] for item in values}) == len({item[1] for item in values})


def _consistent_symbols(
    snapshot: BroadScannerSnapshot,
    active: tuple[ActiveMarketDataSubscription, ...],
    cooldowns: tuple[SubscriptionCooldown, ...],
) -> bool:
    symbols: dict[str, str] = {}
    values = (
        *((item.instrument_id, item.symbol) for item in snapshot.candidates),
        *((item.instrument_id, item.symbol) for item in active),
        *((item.instrument_id, item.symbol) for item in cooldowns),
    )
    for instrument_id, symbol in values:
        existing = symbols.get(instrument_id)
        if existing is not None and existing != symbol:
            return False
        symbols[instrument_id] = symbol
    return True


def _valid_identity(instrument_id: str, symbol: str) -> bool:
    return (
        type(instrument_id) is str
        and _INSTRUMENT_ID.fullmatch(instrument_id) is not None
        and type(symbol) is str
        and _SYMBOL.fullmatch(symbol) is not None
    )


def _positive_duration(value: dt.timedelta) -> bool:
    return type(value) is dt.timedelta and dt.timedelta(0) < value <= dt.timedelta(days=1)


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
