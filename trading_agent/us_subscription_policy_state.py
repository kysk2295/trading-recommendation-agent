from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Final, TypedDict, override

from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_subscription_models import (
    ActiveMarketDataSubscription,
    SubscriptionCooldown,
    SubscriptionPolicyDecision,
    SubscriptionPolicyStatus,
)

_HEX: Final = re.compile(r"^[0-9a-f]{64}$")
_INSTRUMENT_ID: Final = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SYMBOL: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


class SubscriptionPolicyStateError(ValueError):
    @override
    def __str__(self) -> str:
        return "subscription policy state is invalid"


class _ActivePayload(TypedDict):
    instrument_id: str
    subscribed_at: str
    symbol: str


class _CooldownPayload(TypedDict):
    eligible_after: str
    instrument_id: str
    symbol: str


class _StatePayload(TypedDict):
    active: list[_ActivePayload]
    cooldowns: list[_CooldownPayload]
    decision_sha256: str
    evaluated_at: str
    state_id: str


@dataclass(frozen=True, slots=True)
class SubscriptionPolicyRuntimeState:
    state_id: str
    decision_sha256: str
    evaluated_at: dt.datetime
    active: tuple[ActiveMarketDataSubscription, ...]
    cooldowns: tuple[SubscriptionCooldown, ...]


def advance_subscription_policy_state(
    prior: SubscriptionPolicyRuntimeState | None,
    decision: SubscriptionPolicyDecision,
) -> SubscriptionPolicyRuntimeState:
    try:
        _validate_decision(decision)
        if prior is not None:
            validate_subscription_policy_state(prior)
            if prior.evaluated_at > decision.evaluated_at:
                raise SubscriptionPolicyStateError
        prior_active = {} if prior is None else {item.instrument_id: item for item in prior.active}
        active = tuple(
            ActiveMarketDataSubscription(
                item.instrument_id,
                item.symbol,
                _subscribed_at(prior_active.get(item.instrument_id), item.symbol, decision.evaluated_at),
            )
            for item in decision.desired
        )
        desired_ids = {item.instrument_id for item in decision.desired}
        cooldown_by_id = {
            item.instrument_id: item
            for item in (() if prior is None else prior.cooldowns)
            if item.eligible_after > decision.evaluated_at and item.instrument_id not in desired_ids
        }
        for item in decision.new_cooldowns:
            existing = cooldown_by_id.get(item.instrument_id)
            if existing is not None and existing != item:
                raise SubscriptionPolicyStateError
            cooldown_by_id[item.instrument_id] = item
        provisional = SubscriptionPolicyRuntimeState(
            "0" * 64,
            _decision_sha256(decision),
            decision.evaluated_at,
            active,
            tuple(sorted(cooldown_by_id.values(), key=lambda item: item.instrument_id)),
        )
        state = replace(provisional, state_id=_state_sha256(provisional))
        validate_subscription_policy_state(state)
        return state
    except (AttributeError, TypeError, ValueError):
        raise SubscriptionPolicyStateError from None


def validate_subscription_policy_state(state: SubscriptionPolicyRuntimeState) -> None:
    try:
        if (
            type(state) is not SubscriptionPolicyRuntimeState
            or _HEX.fullmatch(state.state_id) is None
            or _HEX.fullmatch(state.decision_sha256) is None
            or not _aware(state.evaluated_at)
            or type(state.active) is not tuple
            or type(state.cooldowns) is not tuple
            or not _valid_active(state.active, state.evaluated_at)
            or not _valid_cooldowns(state.cooldowns, state.evaluated_at)
            or {item.instrument_id for item in state.active} & {item.instrument_id for item in state.cooldowns}
            or state.state_id != _state_sha256(state)
        ):
            raise SubscriptionPolicyStateError
    except (AttributeError, TypeError, ValueError):
        raise SubscriptionPolicyStateError from None


def state_bytes(state: SubscriptionPolicyRuntimeState) -> bytes:
    validate_subscription_policy_state(state)
    return _canonical_bytes(_payload(state)) + b"\n"


def state_from_bytes(value: bytes) -> SubscriptionPolicyRuntimeState:
    try:
        payload = json.loads(value)
        if type(payload) is not dict or set(payload) != _STATE_KEYS:
            raise SubscriptionPolicyStateError
        active = tuple(
            ActiveMarketDataSubscription(
                item["instrument_id"],
                item["symbol"],
                dt.datetime.fromisoformat(item["subscribed_at"]),
            )
            for item in payload["active"]
        )
        cooldowns = tuple(
            SubscriptionCooldown(
                item["instrument_id"],
                item["symbol"],
                dt.datetime.fromisoformat(item["eligible_after"]),
            )
            for item in payload["cooldowns"]
        )
        state = SubscriptionPolicyRuntimeState(
            payload["state_id"],
            payload["decision_sha256"],
            dt.datetime.fromisoformat(payload["evaluated_at"]),
            active,
            cooldowns,
        )
        validate_subscription_policy_state(state)
        if state_bytes(state) != value:
            raise SubscriptionPolicyStateError
        return state
    except (AttributeError, KeyError, TypeError, ValueError):
        raise SubscriptionPolicyStateError from None


def _validate_decision(decision: SubscriptionPolicyDecision) -> None:
    if (
        type(decision) is not SubscriptionPolicyDecision
        or type(decision.identity) is not ResearchInputIdentity
        or decision.status is not SubscriptionPolicyStatus.READY
        or not _aware(decision.evaluated_at)
        or not _aware(decision.candidate_observed_at)
        or decision.candidate_observed_at > decision.evaluated_at
        or len({item.instrument_id for item in decision.desired}) != len(decision.desired)
    ):
        raise SubscriptionPolicyStateError


def _subscribed_at(
    prior: ActiveMarketDataSubscription | None,
    symbol: str,
    evaluated_at: dt.datetime,
) -> dt.datetime:
    if prior is None:
        return evaluated_at
    if prior.symbol != symbol:
        raise SubscriptionPolicyStateError
    return prior.subscribed_at


def _decision_sha256(decision: SubscriptionPolicyDecision) -> str:
    payload = {
        "actions": [
            [item.kind.value, item.instrument_id, item.symbol, [channel.value for channel in item.channels]]
            for item in decision.actions
        ],
        "candidate_observed_at": decision.candidate_observed_at.isoformat(),
        "config": [
            decision.config.capacity,
            _duration_micros(decision.config.max_candidate_age),
            _duration_micros(decision.config.minimum_residency),
            _duration_micros(decision.config.eviction_cooldown),
        ],
        "desired": [
            [item.instrument_id, item.symbol, [channel.value for channel in item.channels]] for item in decision.desired
        ],
        "evaluated_at": decision.evaluated_at.isoformat(),
        "identity_sha256": decision.identity.identity_sha256,
        "new_cooldowns": [
            [item.instrument_id, item.symbol, item.eligible_after.isoformat()] for item in decision.new_cooldowns
        ],
        "policy_semantic_version": decision.policy_semantic_version,
        "status": decision.status.value,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _duration_micros(value: dt.timedelta) -> int:
    return value // dt.timedelta(microseconds=1)


def _valid_active(values: tuple[ActiveMarketDataSubscription, ...], evaluated_at: dt.datetime) -> bool:
    return _valid_identity_set(tuple((item.instrument_id, item.symbol) for item in values)) and not any(
        type(item) is not ActiveMarketDataSubscription
        or not _aware(item.subscribed_at)
        or item.subscribed_at > evaluated_at
        for item in values
    )


def _valid_cooldowns(values: tuple[SubscriptionCooldown, ...], evaluated_at: dt.datetime) -> bool:
    return _valid_identity_set(tuple((item.instrument_id, item.symbol) for item in values)) and not any(
        type(item) is not SubscriptionCooldown or not _aware(item.eligible_after) or item.eligible_after <= evaluated_at
        for item in values
    )


def _valid_identity_set(values: tuple[tuple[str, str], ...]) -> bool:
    return (
        len(values) == len({item[0] for item in values}) == len({item[1] for item in values})
        and all(_INSTRUMENT_ID.fullmatch(item[0]) is not None for item in values)
        and all(_SYMBOL.fullmatch(item[1]) is not None for item in values)
    )


def _payload(state: SubscriptionPolicyRuntimeState) -> _StatePayload:
    return {
        "active": [
            {
                "instrument_id": item.instrument_id,
                "subscribed_at": item.subscribed_at.isoformat(),
                "symbol": item.symbol,
            }
            for item in state.active
        ],
        "cooldowns": [
            {
                "eligible_after": item.eligible_after.isoformat(),
                "instrument_id": item.instrument_id,
                "symbol": item.symbol,
            }
            for item in state.cooldowns
        ],
        "decision_sha256": state.decision_sha256,
        "evaluated_at": state.evaluated_at.isoformat(),
        "state_id": state.state_id,
    }


def _state_sha256(state: SubscriptionPolicyRuntimeState) -> str:
    payload = _payload(state)
    payload["state_id"] = ""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _canonical_bytes(value: _StatePayload) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


_STATE_KEYS = {"active", "cooldowns", "decision_sha256", "evaluated_at", "state_id"}


__all__ = (
    "SubscriptionPolicyRuntimeState",
    "SubscriptionPolicyStateError",
    "advance_subscription_policy_state",
    "state_bytes",
    "state_from_bytes",
    "validate_subscription_policy_state",
)
