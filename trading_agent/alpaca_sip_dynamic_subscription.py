from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import TypedDict, override

from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipTradeStreamProtocolError,
    parse_alpaca_sip_dynamic_subscription_frame,
)
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_subscription_models import (
    DesiredMarketDataSubscription,
    SubscriptionChannel,
    SubscriptionPolicyConfig,
    SubscriptionPolicyDecision,
    SubscriptionPolicyStatus,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_POLICY_VERSION = "us_dynamic_quote_trade_v1"


class AlpacaSipDynamicSubscriptionError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic subscription is invalid"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicBinding:
    instrument_id: str
    symbol: str

    def __post_init__(self) -> None:
        if not self.instrument_id or _SYMBOL.fullmatch(self.symbol) is None:
            raise AlpacaSipDynamicSubscriptionError


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicSubscriptionPlan:
    plan_id: str
    policy_identity_sha256: str
    policy_semantic_version: str
    evaluated_at: dt.datetime
    market_date: dt.date
    bindings: tuple[AlpacaSipDynamicBinding, ...]

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(binding.symbol for binding in self.bindings)


class _BindingPayload(TypedDict):
    instrument_id: str
    symbol: str


class _PlanPayload(TypedDict):
    bindings: list[_BindingPayload]
    evaluated_at: str
    market_date: str
    plan_id: str
    policy_identity_sha256: str
    policy_semantic_version: str


def build_alpaca_sip_dynamic_subscription_plan(
    decision: SubscriptionPolicyDecision,
) -> AlpacaSipDynamicSubscriptionPlan:
    if not _valid_decision(decision):
        raise AlpacaSipDynamicSubscriptionError
    provisional = AlpacaSipDynamicSubscriptionPlan(
        "0" * 64,
        decision.identity.identity_sha256,
        decision.policy_semantic_version,
        decision.evaluated_at,
        decision.evaluated_at.astimezone(NEW_YORK).date(),
        tuple(AlpacaSipDynamicBinding(item.instrument_id, item.symbol) for item in decision.desired),
    )
    plan = replace(provisional, plan_id=_plan_id(provisional))
    _validate_plan(plan)
    return plan


def dynamic_subscription_request_bytes(plan: AlpacaSipDynamicSubscriptionPlan) -> bytes:
    _validate_plan(plan)
    return json.dumps(
        {"action": "subscribe", "quotes": plan.symbols, "trades": plan.symbols},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()


def validate_dynamic_subscription_ack(payload: bytes, plan: AlpacaSipDynamicSubscriptionPlan) -> None:
    _validate_plan(plan)
    try:
        parse_alpaca_sip_dynamic_subscription_frame(payload, plan.symbols)
    except AlpacaSipTradeStreamProtocolError:
        raise AlpacaSipDynamicSubscriptionError from None


def _valid_decision(decision: SubscriptionPolicyDecision) -> bool:
    if (
        type(decision) is not SubscriptionPolicyDecision
        or type(decision.identity) is not ResearchInputIdentity
        or type(decision.config) is not SubscriptionPolicyConfig
        or decision.status is not SubscriptionPolicyStatus.READY
        or decision.policy_semantic_version != _POLICY_VERSION
        or not _aware(decision.evaluated_at)
        or not decision.desired
        or len(decision.desired) > decision.config.capacity
        or any(type(item) is not DesiredMarketDataSubscription for item in decision.desired)
    ):
        return False
    ids = tuple(item.instrument_id for item in decision.desired)
    symbols = tuple(item.symbol for item in decision.desired)
    channels = (SubscriptionChannel.QUOTE, SubscriptionChannel.TRADE)
    return (
        len(ids) == len(set(ids))
        and len(symbols) == len(set(symbols))
        and all(item.channels == channels for item in decision.desired)
    )


def _validate_plan(plan: AlpacaSipDynamicSubscriptionPlan) -> None:
    if (
        type(plan) is not AlpacaSipDynamicSubscriptionPlan
        or _HEX64.fullmatch(plan.plan_id) is None
        or _HEX64.fullmatch(plan.policy_identity_sha256) is None
        or plan.policy_semantic_version != _POLICY_VERSION
        or not _aware(plan.evaluated_at)
        or type(plan.market_date) is not dt.date
        or isinstance(plan.market_date, dt.datetime)
        or plan.market_date != plan.evaluated_at.astimezone(NEW_YORK).date()
        or not plan.bindings
        or any(type(item) is not AlpacaSipDynamicBinding for item in plan.bindings)
        or len(plan.symbols) != len(set(plan.symbols))
        or len({item.instrument_id for item in plan.bindings}) != len(plan.bindings)
        or plan.plan_id != _plan_id(plan)
    ):
        raise AlpacaSipDynamicSubscriptionError


def _plan_id(plan: AlpacaSipDynamicSubscriptionPlan) -> str:
    payload = _payload(plan)
    payload["plan_id"] = ""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _payload(plan: AlpacaSipDynamicSubscriptionPlan) -> _PlanPayload:
    return {
        "bindings": [{"instrument_id": item.instrument_id, "symbol": item.symbol} for item in plan.bindings],
        "evaluated_at": plan.evaluated_at.isoformat(),
        "market_date": plan.market_date.isoformat(),
        "plan_id": plan.plan_id,
        "policy_identity_sha256": plan.policy_identity_sha256,
        "policy_semantic_version": plan.policy_semantic_version,
    }


def _canonical_bytes(payload: _PlanPayload) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicBinding",
    "AlpacaSipDynamicSubscriptionError",
    "AlpacaSipDynamicSubscriptionPlan",
    "build_alpaca_sip_dynamic_subscription_plan",
    "dynamic_subscription_request_bytes",
    "validate_dynamic_subscription_ack",
)
