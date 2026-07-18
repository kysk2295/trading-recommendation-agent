from __future__ import annotations

import datetime as dt
from dataclasses import fields
from decimal import Decimal

import pytest

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_dynamic_subscription_policy import (
    ActiveMarketDataSubscription,
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionActionKind,
    SubscriptionChannel,
    SubscriptionCooldown,
    SubscriptionPolicyConfig,
    SubscriptionPolicyError,
    SubscriptionPolicyStatus,
    build_subscription_policy_decision,
)

_UTC = dt.UTC
_NOW = dt.datetime(2026, 7, 17, 14, 0, tzinfo=_UTC)


def _identity() -> ResearchInputIdentity:
    replay = CanonicalDatasetReplay(
        dataset_id="ds_subscription_fixture",
        event_count=3,
        canonical_event_content_sha256="a" * 64,
        parquet_sha256="c" * 64,
        raw_manifest_id="raw_subscription_fixture",
        raw_manifest_content_sha256="b" * 64,
    )
    return ResearchInputIdentity.from_verified_replay(
        "us_equities.opportunity.dynamic_subscription",
        replay,
    )


def _candidate(
    instrument_id: str,
    symbol: str,
    score: str,
    rank: int,
) -> BroadScannerCandidate:
    return BroadScannerCandidate(
        instrument_id=instrument_id,
        symbol=symbol,
        priority_score=Decimal(score),
        source_rank=rank,
    )


def _snapshot(
    *candidates: BroadScannerCandidate,
    observed_at: dt.datetime = _NOW - dt.timedelta(seconds=10),
) -> BroadScannerSnapshot:
    return BroadScannerSnapshot(
        identity=_identity(),
        observed_at=observed_at,
        candidates=candidates,
    )


def _config(
    *,
    capacity: int = 2,
    minimum_residency: dt.timedelta = dt.timedelta(minutes=2),
    eviction_cooldown: dt.timedelta = dt.timedelta(minutes=5),
) -> SubscriptionPolicyConfig:
    return SubscriptionPolicyConfig(
        capacity=capacity,
        max_candidate_age=dt.timedelta(seconds=30),
        minimum_residency=minimum_residency,
        eviction_cooldown=eviction_cooldown,
    )


def _active(
    candidate: BroadScannerCandidate,
    *,
    subscribed_at: dt.datetime = _NOW - dt.timedelta(minutes=10),
) -> ActiveMarketDataSubscription:
    return ActiveMarketDataSubscription(
        instrument_id=candidate.instrument_id,
        symbol=candidate.symbol,
        subscribed_at=subscribed_at,
    )


def test_candidate_is_declarative_and_policy_emits_bounded_quote_trade_set() -> None:
    first = _candidate("us-eq-a", "AAA", "9.5", 2)
    second = _candidate("us-eq-b", "BBB", "10", 4)
    third = _candidate("us-eq-c", "CCC", "8", 1)

    decision = build_subscription_policy_decision(
        _snapshot(first, second, third),
        evaluated_at=_NOW,
        active=(),
        cooldowns=(),
        config=_config(),
    )

    assert tuple(field.name for field in fields(BroadScannerCandidate)) == (
        "instrument_id",
        "symbol",
        "priority_score",
        "source_rank",
    )
    assert decision.status is SubscriptionPolicyStatus.READY
    assert decision.identity == _identity()
    assert decision.policy_semantic_version == "us_dynamic_quote_trade_v1"
    assert decision.config == _config()
    assert tuple(item.instrument_id for item in decision.desired) == ("us-eq-b", "us-eq-a")
    assert all(
        item.channels == (SubscriptionChannel.QUOTE, SubscriptionChannel.TRADE)
        for item in decision.desired
    )
    assert tuple(action.kind for action in decision.actions) == (
        SubscriptionActionKind.SUBSCRIBE,
        SubscriptionActionKind.SUBSCRIBE,
    )


def test_ranking_ties_are_stable_under_input_reordering() -> None:
    a = _candidate("us-eq-a", "AAA", "10", 1)
    b = _candidate("us-eq-b", "BBB", "10", 1)
    c = _candidate("us-eq-c", "CCC", "10", 1)

    first = build_subscription_policy_decision(
        _snapshot(c, a, b),
        evaluated_at=_NOW,
        active=(),
        cooldowns=(),
        config=_config(),
    )
    second = build_subscription_policy_decision(
        _snapshot(b, c, a),
        evaluated_at=_NOW,
        active=(),
        cooldowns=(),
        config=_config(),
    )

    assert first == second
    assert tuple(item.instrument_id for item in first.desired) == ("us-eq-a", "us-eq-b")


def test_minimum_residency_protects_incumbent_under_capacity_pressure() -> None:
    incumbent = _candidate("us-eq-incumbent", "INC", "1", 20)
    challenger = _candidate("us-eq-challenger", "NEW", "100", 1)

    decision = build_subscription_policy_decision(
        _snapshot(incumbent, challenger),
        evaluated_at=_NOW,
        active=(_active(incumbent, subscribed_at=_NOW - dt.timedelta(seconds=30)),),
        cooldowns=(),
        config=_config(capacity=1),
    )

    assert tuple(item.instrument_id for item in decision.desired) == (incumbent.instrument_id,)
    assert decision.actions == ()


def test_expired_incumbent_is_deterministically_evicted_before_challenger_subscribe() -> None:
    incumbent = _candidate("us-eq-incumbent", "INC", "1", 20)
    challenger = _candidate("us-eq-challenger", "NEW", "100", 1)

    decision = build_subscription_policy_decision(
        _snapshot(incumbent, challenger),
        evaluated_at=_NOW,
        active=(_active(incumbent),),
        cooldowns=(),
        config=_config(capacity=1),
    )

    assert tuple(item.instrument_id for item in decision.desired) == (challenger.instrument_id,)
    assert tuple((action.kind, action.instrument_id) for action in decision.actions) == (
        (SubscriptionActionKind.UNSUBSCRIBE, incumbent.instrument_id),
        (SubscriptionActionKind.SUBSCRIBE, challenger.instrument_id),
    )
    assert decision.new_cooldowns == (
        SubscriptionCooldown(
            instrument_id=incumbent.instrument_id,
            symbol=incumbent.symbol,
            eligible_after=_NOW + dt.timedelta(minutes=5),
        ),
    )


def test_hard_capacity_deterministically_overrides_minimum_residency() -> None:
    first = _candidate("us-eq-a", "AAA", "10", 1)
    second = _candidate("us-eq-b", "BBB", "9", 2)

    decision = build_subscription_policy_decision(
        _snapshot(second, first),
        evaluated_at=_NOW,
        active=(
            _active(second, subscribed_at=_NOW - dt.timedelta(seconds=10)),
            _active(first, subscribed_at=_NOW - dt.timedelta(seconds=10)),
        ),
        cooldowns=(),
        config=_config(capacity=1),
    )

    assert tuple(item.instrument_id for item in decision.desired) == (first.instrument_id,)
    assert tuple((action.kind, action.instrument_id) for action in decision.actions) == (
        (SubscriptionActionKind.UNSUBSCRIBE, second.instrument_id),
    )


def test_cooldown_blocks_reentry_until_exact_eligibility_time() -> None:
    candidate = _candidate("us-eq-a", "AAA", "10", 1)
    cooldown = SubscriptionCooldown(
        instrument_id=candidate.instrument_id,
        symbol=candidate.symbol,
        eligible_after=_NOW + dt.timedelta(seconds=1),
    )

    blocked = build_subscription_policy_decision(
        _snapshot(candidate),
        evaluated_at=_NOW,
        active=(),
        cooldowns=(cooldown,),
        config=_config(capacity=1),
    )
    eligible = build_subscription_policy_decision(
        _snapshot(candidate, observed_at=_NOW),
        evaluated_at=cooldown.eligible_after,
        active=(),
        cooldowns=(cooldown,),
        config=_config(capacity=1),
    )

    assert blocked.desired == ()
    assert blocked.actions == ()
    assert tuple(item.instrument_id for item in eligible.desired) == (candidate.instrument_id,)


@pytest.mark.parametrize(
    ("evaluated_at", "observed_at", "status"),
    (
        (
            _NOW,
            _NOW - dt.timedelta(seconds=31),
            SubscriptionPolicyStatus.BLOCKED_STALE,
        ),
        (
            dt.datetime(2026, 7, 17, 13, 29, tzinfo=_UTC),
            dt.datetime(2026, 7, 17, 13, 29, tzinfo=_UTC),
            SubscriptionPolicyStatus.BLOCKED_SESSION_CLOSED,
        ),
    ),
)
def test_stale_or_closed_session_has_no_desired_subscription(
    evaluated_at: dt.datetime,
    observed_at: dt.datetime,
    status: SubscriptionPolicyStatus,
) -> None:
    candidate = _candidate("us-eq-a", "AAA", "10", 1)

    decision = build_subscription_policy_decision(
        _snapshot(candidate, observed_at=observed_at),
        evaluated_at=evaluated_at,
        active=(_active(candidate, subscribed_at=evaluated_at - dt.timedelta(minutes=10)),),
        cooldowns=(),
        config=_config(capacity=1),
    )

    assert decision.status is status
    assert decision.desired == ()
    assert tuple(action.kind for action in decision.actions) == (
        SubscriptionActionKind.UNSUBSCRIBE,
    )
    assert decision.new_cooldowns == ()


def test_missing_incumbent_candidate_is_removed_and_cooled_down() -> None:
    incumbent = _candidate("us-eq-incumbent", "INC", "1", 20)

    decision = build_subscription_policy_decision(
        _snapshot(),
        evaluated_at=_NOW,
        active=(_active(incumbent),),
        cooldowns=(),
        config=_config(capacity=1),
    )

    assert decision.status is SubscriptionPolicyStatus.READY
    assert decision.desired == ()
    assert tuple(action.kind for action in decision.actions) == (
        SubscriptionActionKind.UNSUBSCRIBE,
    )
    assert decision.new_cooldowns[0].instrument_id == incumbent.instrument_id


@pytest.mark.parametrize(
    "snapshot",
    (
        BroadScannerSnapshot(
            identity=_identity(),
            observed_at=_NOW,
            candidates=(
                _candidate("us-eq-a", "AAA", "10", 1),
                _candidate("us-eq-a", "AAA", "9", 2),
            ),
        ),
        BroadScannerSnapshot(
            identity=_identity(),
            observed_at=_NOW.replace(tzinfo=None),
            candidates=(),
        ),
        BroadScannerSnapshot(
            identity=_identity(),
            observed_at=_NOW + dt.timedelta(seconds=1),
            candidates=(),
        ),
    ),
)
def test_invalid_or_future_scanner_snapshot_fails_closed(snapshot: BroadScannerSnapshot) -> None:
    with pytest.raises(SubscriptionPolicyError, match="subscription policy input is invalid"):
        _ = build_subscription_policy_decision(
            snapshot,
            evaluated_at=_NOW,
            active=(),
            cooldowns=(),
            config=_config(),
        )


def test_inconsistent_active_and_cooldown_state_fails_closed() -> None:
    candidate = _candidate("us-eq-a", "AAA", "10", 1)
    active = _active(candidate)
    cooldown = SubscriptionCooldown(
        instrument_id=candidate.instrument_id,
        symbol=candidate.symbol,
        eligible_after=_NOW + dt.timedelta(minutes=5),
    )

    with pytest.raises(SubscriptionPolicyError, match="subscription policy input is invalid"):
        _ = build_subscription_policy_decision(
            _snapshot(candidate),
            evaluated_at=_NOW,
            active=(active,),
            cooldowns=(cooldown,),
            config=_config(capacity=1),
        )


def test_symbol_alias_mismatch_across_scanner_and_active_state_fails_closed() -> None:
    candidate = _candidate("us-eq-a", "AAA", "10", 1)
    mismatched = ActiveMarketDataSubscription(
        instrument_id=candidate.instrument_id,
        symbol="DIFF",
        subscribed_at=_NOW - dt.timedelta(minutes=5),
    )

    with pytest.raises(SubscriptionPolicyError, match="subscription policy input is invalid"):
        _ = build_subscription_policy_decision(
            _snapshot(candidate),
            evaluated_at=_NOW,
            active=(mismatched,),
            cooldowns=(),
            config=_config(capacity=1),
        )
