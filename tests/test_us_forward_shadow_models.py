from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from trading_agent.day_forward_trial_identity import DayForwardExitReason
from trading_agent.generated_strategy_protocol import BarFrame
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    QuoteValidation,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
    TradeTarget,
)
from trading_agent.us_forward_shadow_artifacts import (
    InvalidUsForwardShadowArtifactError,
    UsForwardShadowArtifactStore,
    UsForwardShadowOutcomeArtifact,
    UsForwardShadowOutcomeLeg,
    UsForwardShadowSignalArtifact,
    build_us_forward_shadow_outcome_artifact,
    build_us_forward_shadow_signal_artifact,
)
from trading_agent.us_forward_shadow_models import (
    UsForwardShadowTick,
    completed_bar_id,
    current_xnys_tick_at,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
type TestValue = (
    None
    | bool
    | int
    | float
    | str
    | Decimal
    | dt.date
    | dt.datetime
    | MarketId
    | list[TestValue]
    | dict[str, TestValue]
)


def test_tick_accepts_only_fresh_current_xnys_completed_bar() -> None:
    # Given a canonical tick observed during the current XNYS regular session.
    payload = _tick_payload()

    # When the boundary validates the payload.
    tick = UsForwardShadowTick.model_validate(payload)

    # Then the latest warmup bar is the only declared completed bar identity.
    assert tick.completed_bar_id == completed_bar_id(tick.bars[-1])
    assert tick.market_id is MarketId.US_EQUITIES


def test_tick_rejects_quote_older_than_five_seconds_despite_extended_valid_until() -> None:
    # Given a quote whose claimed validity has been extended beyond its real observation age.
    payload = _tick_payload()
    quote = cast(dict[str, TestValue], payload["quote"])
    quote["observed_at"] = dt.datetime(2026, 8, 20, 14, 1, 20, tzinfo=dt.UTC)
    quote["valid_until"] = dt.datetime(2026, 8, 20, 14, 10, tzinfo=dt.UTC)

    # When the snapshot is evaluated ten seconds after the quote observation.
    with pytest.raises((ValidationError, ValueError), match="us_forward_shadow_tick_invalid"):
        UsForwardShadowTick.model_validate(payload)

    # Then untrusted valid_until cannot make a stale quote current.


def test_current_tick_requires_the_last_completed_minute_and_admits_opening_bar() -> None:
    payload = _tick_payload()
    tick = UsForwardShadowTick.model_validate(payload)

    assert current_xnys_tick_at(tick, dt.datetime(2026, 8, 20, 14, 1, 30, tzinfo=dt.UTC))

    forming = _tick_payload()
    bars = cast(list[TestValue], forming["bars"])
    latest = cast(dict[str, TestValue], bars[-1])
    latest["timestamp"] = dt.datetime(2026, 8, 20, 14, 1, tzinfo=dt.UTC)
    forming["completed_bar_id"] = completed_bar_id(BarFrame.model_validate(latest))
    cast(dict[str, TestValue], forming["candidate"])["timestamp"] = latest["timestamp"]
    with pytest.raises((ValidationError, ValueError), match="us_forward_shadow_tick_invalid"):
        UsForwardShadowTick.model_validate(forming)

    opening = _tick_payload()
    opening_bar = _bar(dt.datetime(2026, 8, 20, 13, 30, tzinfo=dt.UTC), 100.0)
    opening["bars"] = [opening_bar]
    opening["completed_bar_id"] = completed_bar_id(BarFrame.model_validate(opening_bar))
    cast(dict[str, TestValue], opening["candidate"])["timestamp"] = opening_bar["timestamp"]
    cast(dict[str, TestValue], opening["quote"])["observed_at"] = dt.datetime(
        2026, 8, 20, 13, 31, tzinfo=dt.UTC
    )
    cast(dict[str, TestValue], opening["quote"])["valid_until"] = dt.datetime(
        2026, 8, 20, 13, 31, 10, tzinfo=dt.UTC
    )
    evidence = cast(list[TestValue], opening["evidence_refs"])
    cast(dict[str, TestValue], evidence[0])["observed_at"] = opening_bar["timestamp"]
    opening["observed_at"] = dt.datetime(2026, 8, 20, 13, 31, tzinfo=dt.UTC)

    assert current_xnys_tick_at(
        UsForwardShadowTick.model_validate(opening),
        dt.datetime(2026, 8, 20, 13, 31, tzinfo=dt.UTC),
    )


@pytest.mark.parametrize("mutation", ("closed", "stale", "future", "cross_session"))
def test_tick_rejects_closed_stale_future_and_cross_session_inputs(mutation: str) -> None:
    # Given one unsafe mutation of an otherwise valid current-session tick.
    payload = _tick_payload()
    if mutation == "closed":
        payload["observed_at"] = dt.datetime(2026, 8, 20, 21, 0, tzinfo=dt.UTC)
    elif mutation == "stale":
        payload["observed_at"] = dt.datetime(2026, 8, 20, 14, 3, tzinfo=dt.UTC)
    elif mutation == "future":
        bars = cast(list[TestValue], payload["bars"])
        latest = cast(dict[str, TestValue], bars[-1])
        latest["timestamp"] = dt.datetime(2026, 8, 20, 14, 2, tzinfo=dt.UTC)
        payload["completed_bar_id"] = completed_bar_id(BarFrame.model_validate(latest))
    else:
        payload["session_date"] = dt.date(2026, 8, 19)
        payload["session_id"] = "XNYS-2026-08-19"

    # When the strict input model validates it, then it fails closed.
    with pytest.raises((ValidationError, ValueError), match="us_forward_shadow_tick_invalid"):
        UsForwardShadowTick.model_validate(payload)


def test_tick_rejects_non_us_wrong_policy_bar_candidate_quote_and_evidence() -> None:
    # Given independently malformed authority and lineage fields.
    variants: list[dict[str, TestValue]] = []
    for field, value in (
        ("market_id", MarketId.KR_EQUITIES),
        ("policy_id", "not-a-policy"),
        ("completed_bar_id", SHA_B),
    ):
        changed = _tick_payload()
        changed[field] = value
        variants.append(changed)
    wrong_candidate = _tick_payload()
    cast(dict[str, TestValue], wrong_candidate["candidate"])["symbol"] = "MSFT"
    variants.append(wrong_candidate)
    expired_quote = _tick_payload()
    cast(dict[str, TestValue], expired_quote["quote"])["valid_until"] = dt.datetime(
        2026, 8, 20, 14, 1, 10, tzinfo=dt.UTC
    )
    variants.append(expired_quote)
    future_evidence = _tick_payload()
    evidence = cast(list[TestValue], future_evidence["evidence_refs"])
    cast(dict[str, TestValue], evidence[0])["observed_at"] = dt.datetime(
        2026, 8, 20, 14, 2, tzinfo=dt.UTC
    )
    variants.append(future_evidence)

    # When each variant crosses the boundary, then none is accepted.
    for changed in variants:
        with pytest.raises((ValidationError, ValueError)):
            UsForwardShadowTick.model_validate(changed)


def test_shadow_artifact_store_is_private_immutable_and_idempotent(tmp_path: Path) -> None:
    # Given canonical signal and modeled outcome artifacts.
    store = UsForwardShadowArtifactStore(tmp_path / "shadow")
    signal = _signal_artifact()
    outcome = _outcome_artifact()

    # When artifacts are published twice with the same canonical content.
    assert store.publish_signal(signal) is True
    assert store.publish_signal(signal) is False
    assert store.publish_outcome(outcome) is True
    assert store.publish_outcome(outcome) is False

    # Then exact replay loads the originals and private modes are retained.
    assert store.signal(signal.trial_id) == signal
    assert store.outcome(outcome.outcome_id) == outcome
    assert (store.root / "signals" / f"{signal.trial_id}.json").stat().st_mode & 0o777 == 0o600


def test_shadow_artifact_store_rejects_conflict_tamper_symlink_and_wrong_mode(tmp_path: Path) -> None:
    # Given four stores with unsafe existing filesystem state.
    signal = _signal_artifact()
    conflict_store = UsForwardShadowArtifactStore(tmp_path / "conflict")
    assert conflict_store.publish_signal(signal)
    conflict = signal.model_copy(update={"completed_bar_id": SHA_B})
    tamper_store = UsForwardShadowArtifactStore(tmp_path / "tamper")
    assert tamper_store.publish_signal(signal)
    tamper_path = tamper_store.root / "signals" / f"{signal.trial_id}.json"
    tamper_path.write_text("{}\n")
    os.chmod(tamper_path, 0o600)
    symlink_store = UsForwardShadowArtifactStore(tmp_path / "symlink")
    symlink_path = symlink_store.root / "signals" / f"{signal.trial_id}.json"
    symlink_path.parent.mkdir(parents=True, mode=0o700)
    symlink_path.symlink_to(tamper_path)
    mode_store = UsForwardShadowArtifactStore(tmp_path / "mode")
    assert mode_store.publish_signal(signal)
    os.chmod(mode_store.root / "signals" / f"{signal.trial_id}.json", 0o644)

    # When publication or loading encounters those paths, then it fails closed.
    for operation in (
        lambda: conflict_store.publish_signal(conflict),
        lambda: tamper_store.signal(signal.trial_id),
        lambda: symlink_store.signal(signal.trial_id),
        lambda: mode_store.signal(signal.trial_id),
    ):
        with pytest.raises(InvalidUsForwardShadowArtifactError):
            operation()


def _tick_payload() -> dict[str, TestValue]:
    first = _bar(dt.datetime(2026, 8, 20, 13, 59, tzinfo=dt.UTC), 100.0)
    latest = _bar(dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC), 101.0)
    quote_at = dt.datetime(2026, 8, 20, 14, 1, 25, tzinfo=dt.UTC)
    return {
        "schema_version": 1,
        "market_id": MarketId.US_EQUITIES,
        "policy_id": SHA_A,
        "session_id": "XNYS-2026-08-20",
        "session_date": dt.date(2026, 8, 20),
        "calendar_snapshot_id": "calendar://official/XNYS/2026-v1",
        "completed_bar_id": completed_bar_id(BarFrame.model_validate(latest)),
        "completed_bar_sequence": 2,
        "bars": [first, latest],
        "candidate": {
            "symbol": "AAPL",
            "timestamp": latest["timestamp"],
            "price": 101.0,
            "gap_pct": 1.0,
            "change_pct": 1.0,
            "relative_volume": 2.0,
            "cumulative_dollar_volume": 1_000_000.0,
            "spread_bps": 9.95025,
            "catalyst": "earnings",
        },
        "quote": {
            "bid": Decimal("100.95"),
            "ask": Decimal("101.05"),
            "observed_at": quote_at,
            "valid_until": quote_at + dt.timedelta(seconds=30),
            "spread_bps": Decimal("9.90099"),
            "max_slippage_bps": Decimal("20"),
        },
        "evidence_refs": [
            {
                "namespace": "market/current_bar",
                "record_id": "AAPL-2026-08-20T14:01:00Z",
                "observed_at": latest["timestamp"],
            }
        ],
        "observed_at": dt.datetime(2026, 8, 20, 14, 1, 30, tzinfo=dt.UTC),
    }


def _bar(timestamp: dt.datetime, close: float) -> dict[str, TestValue]:
    return {
        "symbol": "AAPL",
        "timestamp": timestamp,
        "open": close - 0.2,
        "high": close + 0.3,
        "low": close - 0.4,
        "close": close,
        "volume": 10_000,
        "prior_close": 99.0,
        "average_daily_volume": 1_000_000,
        "spread_bps": 10.0,
        "catalyst": "earnings",
    }


def _signal_artifact() -> UsForwardShadowSignalArtifact:
    observed = dt.datetime(2026, 8, 20, 14, 1, 30, tzinfo=dt.UTC)
    signal = TradeSignalEnvelope(
        signal_id="signal-aapl-1",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="generated_shadow",
        ),
        producer_strategy_version="generated-shadow-v1",
        symbol="AAPL",
        observed_at=observed,
        valid_until=observed + dt.timedelta(minutes=1),
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=Decimal("101"),
        stop_price=Decimal("100"),
        targets=(TradeTarget(label="r1", price=Decimal("102")),),
        actionability=SignalActionability.CURRENT_QUOTE_VALIDATED,
        invalidation_rule="Stop below 100.",
        rationale="Generated research-only breakout signal.",
        evidence_refs=(
            EvidenceRef(namespace="market/current_bar", record_id="bar-1", observed_at=observed),
        ),
        quote_validation=QuoteValidation(
            bid=Decimal("100.95"),
            ask=Decimal("101.05"),
            observed_at=observed,
            valid_until=observed + dt.timedelta(seconds=30),
            spread_bps=Decimal("9.90099"),
            max_slippage_bps=Decimal("20"),
        ),
    )
    return build_us_forward_shadow_signal_artifact(
        trial_id=SHA_A,
        capsule_id=SHA_B,
        completed_bar_id="c" * 64,
        completed_bar_sequence=2,
        signal=signal,
    )


def _outcome_artifact() -> UsForwardShadowOutcomeArtifact:
    return build_us_forward_shadow_outcome_artifact(
        trial_id=SHA_A,
        signal_artifact_id=_signal_artifact().artifact_id,
        exit_completed_bar_id="d" * 64,
        exit_completed_bar_sequence=4,
        entry_price=Decimal("101"),
        legs=(
            UsForwardShadowOutcomeLeg(
                target_label="r1",
                exit_completed_bar_id="d" * 64,
                exit_price=Decimal("102"),
                exit_reason=DayForwardExitReason.TARGET,
                weight=Decimal("0.5"),
                gross_return=Decimal("0"),
            ),
            UsForwardShadowOutcomeLeg(
                target_label="r2",
                exit_completed_bar_id="d" * 64,
                exit_price=Decimal("103"),
                exit_reason=DayForwardExitReason.TARGET,
                weight=Decimal("0.5"),
                gross_return=Decimal("0"),
            ),
        ),
        round_trip_cost_bps=Decimal("5"),
        exit_reason=DayForwardExitReason.TARGET,
        recorded_at=dt.datetime(2026, 8, 20, 14, 5, tzinfo=dt.UTC),
    )
