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
        (
            BroadScannerCandidate("us-eq-a", "AAA", Decimal("9.5"), 2),
            BroadScannerCandidate("us-eq-b", "BBB", Decimal("10"), 4),
        ),
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
