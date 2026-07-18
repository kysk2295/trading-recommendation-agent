"""Immutable contracts for bounded US market-data subscription decisions."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, override

from trading_agent.research_input_identity import ResearchInputIdentity

_ERROR_MESSAGE: Final = "subscription policy input is invalid"


class SubscriptionPolicyError(ValueError):
    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "SubscriptionPolicyError()"


class SubscriptionChannel(StrEnum):
    QUOTE = "quote"
    TRADE = "trade"


class SubscriptionActionKind(StrEnum):
    UNSUBSCRIBE = "unsubscribe"
    SUBSCRIBE = "subscribe"


class SubscriptionPolicyStatus(StrEnum):
    READY = "ready"
    BLOCKED_STALE = "blocked_stale"
    BLOCKED_SESSION_CLOSED = "blocked_session_closed"


@dataclass(frozen=True, slots=True)
class BroadScannerCandidate:
    instrument_id: str
    symbol: str
    priority_score: Decimal
    source_rank: int


@dataclass(frozen=True, slots=True)
class BroadScannerSnapshot:
    identity: ResearchInputIdentity
    observed_at: dt.datetime
    candidates: tuple[BroadScannerCandidate, ...]


@dataclass(frozen=True, slots=True)
class ActiveMarketDataSubscription:
    instrument_id: str
    symbol: str
    subscribed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class SubscriptionCooldown:
    instrument_id: str
    symbol: str
    eligible_after: dt.datetime


@dataclass(frozen=True, slots=True)
class SubscriptionPolicyConfig:
    capacity: int
    max_candidate_age: dt.timedelta
    minimum_residency: dt.timedelta
    eviction_cooldown: dt.timedelta


@dataclass(frozen=True, slots=True)
class DesiredMarketDataSubscription:
    instrument_id: str
    symbol: str
    channels: tuple[SubscriptionChannel, ...]


@dataclass(frozen=True, slots=True)
class SubscriptionAction:
    kind: SubscriptionActionKind
    instrument_id: str
    symbol: str
    channels: tuple[SubscriptionChannel, ...]


@dataclass(frozen=True, slots=True)
class SubscriptionPolicyDecision:
    identity: ResearchInputIdentity
    evaluated_at: dt.datetime
    candidate_observed_at: dt.datetime
    status: SubscriptionPolicyStatus
    policy_semantic_version: str
    config: SubscriptionPolicyConfig
    desired: tuple[DesiredMarketDataSubscription, ...]
    actions: tuple[SubscriptionAction, ...]
    new_cooldowns: tuple[SubscriptionCooldown, ...]
