from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionError,
    build_alpaca_sip_dynamic_subscription_plan,
    dynamic_subscription_request_bytes,
    roll_alpaca_sip_dynamic_subscription_plan,
    validate_dynamic_subscription_ack,
)
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_subscription_models import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
)
from trading_agent.us_subscription_policy_state import advance_subscription_policy_state

_NOW = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)


def _ack(symbols: tuple[str, ...]) -> bytes:
    joined = b",".join(f'"{symbol}"'.encode() for symbol in symbols)
    return (
        b'[{"T":"subscription","trades":['
        + joined
        + b'],"quotes":['
        + joined
        + b'],"bars":[],"updatedBars":[],"dailyBars":[],"statuses":[],"lulds":[],'
        + b'"corrections":['
        + joined
        + b'],"cancelErrors":['
        + joined
        + b"]}]"
    )


def test_ready_policy_builds_exact_quote_trade_wire_plan() -> None:
    decision = _decision(_NOW)

    plan = build_alpaca_sip_dynamic_subscription_plan(decision)

    assert plan.market_date == dt.date(2026, 7, 17)
    assert tuple(binding.instrument_id for binding in plan.bindings) == ("us-eq-b", "us-eq-a")
    assert plan.symbols == ("BBB", "AAA")
    assert len(plan.plan_id) == 64
    assert dynamic_subscription_request_bytes(plan) == (
        b'{"action":"subscribe","quotes":["BBB","AAA"],"trades":["BBB","AAA"]}'
    )
    validate_dynamic_subscription_ack(_ack(("BBB", "AAA")), plan)
    validate_dynamic_subscription_ack(_ack(("AAA", "BBB")), plan)


def test_runtime_state_reuses_plan_while_active_topology_is_unchanged() -> None:
    first_decision = _decision(_NOW)
    first_state = advance_subscription_policy_state(None, first_decision)
    first_plan = roll_alpaca_sip_dynamic_subscription_plan(None, first_state)
    later = _NOW + dt.timedelta(minutes=1)
    next_decision = build_subscription_policy_decision(
        BroadScannerSnapshot(_identity(), later - dt.timedelta(seconds=10), _candidates()),
        evaluated_at=later,
        active=first_state.active,
        cooldowns=first_state.cooldowns,
        config=first_decision.config,
    )
    next_state = advance_subscription_policy_state(first_state, next_decision)

    next_plan = roll_alpaca_sip_dynamic_subscription_plan(first_plan, next_state)

    assert next_plan == first_plan
    assert next_plan.evaluated_at == _NOW


def test_runtime_state_rolls_plan_when_active_topology_changes() -> None:
    first_decision = _decision(_NOW)
    first_state = advance_subscription_policy_state(None, first_decision)
    first_plan = roll_alpaca_sip_dynamic_subscription_plan(None, first_state)
    later = _NOW + dt.timedelta(minutes=3)
    changed_decision = build_subscription_policy_decision(
        BroadScannerSnapshot(
            _identity(),
            later - dt.timedelta(seconds=10),
            (BroadScannerCandidate("us-eq-c", "CCC", Decimal("100"), 1),),
        ),
        evaluated_at=later,
        active=first_state.active,
        cooldowns=first_state.cooldowns,
        config=SubscriptionPolicyConfig(
            1,
            dt.timedelta(seconds=30),
            dt.timedelta(minutes=2),
            dt.timedelta(minutes=5),
        ),
    )
    changed_state = advance_subscription_policy_state(first_state, changed_decision)

    changed_plan = roll_alpaca_sip_dynamic_subscription_plan(first_plan, changed_state)

    assert changed_plan.plan_id != first_plan.plan_id
    assert changed_plan.symbols == ("CCC",)
    assert changed_plan.evaluated_at == later


def test_runtime_state_rolls_plan_at_new_market_date() -> None:
    first_state = advance_subscription_policy_state(None, _decision(_NOW))
    first_plan = roll_alpaca_sip_dynamic_subscription_plan(None, first_state)
    next_session = _NOW + dt.timedelta(days=3)
    next_decision = build_subscription_policy_decision(
        BroadScannerSnapshot(_identity(), next_session - dt.timedelta(seconds=10), _candidates()),
        evaluated_at=next_session,
        active=first_state.active,
        cooldowns=first_state.cooldowns,
        config=_decision(_NOW).config,
    )
    next_state = advance_subscription_policy_state(first_state, next_decision)

    next_plan = roll_alpaca_sip_dynamic_subscription_plan(first_plan, next_state)

    assert next_plan.plan_id != first_plan.plan_id
    assert next_plan.market_date == dt.date(2026, 7, 20)
    assert next_plan.evaluated_at == next_session


@pytest.mark.parametrize(
    "payload",
    (
        _ack(("BBB",)),
        _ack(("BBB", "AAA", "CCC")),
        _ack(("BBB", "AAA", "AAA")),
        b'[{"T":"error","code":405,"msg":"symbol limit exceeded"}]',
        (
            b'[{"T":"subscription","trades":["BBB","AAA"],"quotes":[],"bars":[],'
            b'"updatedBars":[],"dailyBars":[],"statuses":[],"lulds":[],'
            b'"corrections":["BBB","AAA"],"cancelErrors":["BBB","AAA"]}]'
        ),
    ),
)
def test_missing_extra_or_partial_subscription_ack_fails_closed(payload: bytes) -> None:
    plan = build_alpaca_sip_dynamic_subscription_plan(_decision(_NOW))

    with pytest.raises(AlpacaSipDynamicSubscriptionError):
        validate_dynamic_subscription_ack(payload, plan)


def test_closed_policy_cannot_build_stream_plan() -> None:
    closed = dt.datetime(2026, 7, 17, 13, 29, tzinfo=dt.UTC)

    with pytest.raises(AlpacaSipDynamicSubscriptionError):
        _ = build_alpaca_sip_dynamic_subscription_plan(_decision(closed))


def test_malformed_nested_policy_config_fails_closed() -> None:
    malformed = replace(
        _decision(_NOW),
        config=cast("SubscriptionPolicyConfig", object()),
    )

    with pytest.raises(AlpacaSipDynamicSubscriptionError):
        _ = build_alpaca_sip_dynamic_subscription_plan(malformed)


def _decision(evaluated_at: dt.datetime):
    snapshot = BroadScannerSnapshot(
        _identity(),
        evaluated_at - dt.timedelta(seconds=10),
        _candidates(),
    )
    return build_subscription_policy_decision(
        snapshot,
        evaluated_at=evaluated_at,
        active=(),
        cooldowns=(),
        config=SubscriptionPolicyConfig(
            2,
            dt.timedelta(seconds=30),
            dt.timedelta(minutes=2),
            dt.timedelta(minutes=5),
        ),
    )


def _candidates() -> tuple[BroadScannerCandidate, ...]:
    return (
        BroadScannerCandidate("us-eq-a", "AAA", Decimal("9.5"), 2),
        BroadScannerCandidate("us-eq-b", "BBB", Decimal("10"), 4),
    )


def _identity() -> ResearchInputIdentity:
    return ResearchInputIdentity.from_verified_replay(
        "us_equities.opportunity.dynamic_subscription",
        CanonicalDatasetReplay(
            "ds_dynamic_stream_fixture",
            2,
            "a" * 64,
            "b" * 64,
            "raw_dynamic_stream_fixture",
            "c" * 64,
        ),
    )
