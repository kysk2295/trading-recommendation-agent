from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.intraday_feature_kernel import CompletedMinuteBar, FeatureSnapshotStatus
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_dynamic_subscription_policy import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
    build_subscription_policy_decision,
)
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeBatch,
    MarketDataRuntimeCheckpoint,
    MarketDataRuntimeIncidentKind,
    MarketDataRuntimeStatus,
    RuntimeFeatureRequest,
    build_market_data_runtime_receipt,
)
from trading_agent.us_market_data_runtime_store import (
    MarketDataRuntimeStore,
    MarketDataWriterLeaseUnavailableError,
)
from trading_agent.us_market_data_supervisor import UsMarketDataSupervisor
from trading_agent.us_subscription_models import (
    DesiredMarketDataSubscription,
    SubscriptionPolicyDecision,
    SubscriptionPolicyStatus,
)

_UTC = dt.UTC
_NOW = dt.datetime(2026, 7, 17, 14, 30, tzinfo=_UTC)
_SOURCE_ID = "fixture.sip.us_equities"
_INSTRUMENT_ID = "us-eq-fixture-aaa"
_SYMBOL = "AAA"


class _FixtureAdapter:
    def __init__(self, batches: Sequence[MarketDataRuntimeBatch]) -> None:
        self.source_id = _SOURCE_ID
        self._batches = list(batches)
        self.calls: list[tuple[tuple[DesiredMarketDataSubscription, ...], MarketDataRuntimeCheckpoint | None]] = []

    def read_batch(
        self,
        desired: tuple[DesiredMarketDataSubscription, ...],
        checkpoint: MarketDataRuntimeCheckpoint | None,
    ) -> MarketDataRuntimeBatch:
        self.calls.append((desired, checkpoint))
        return self._batches.pop(0)


def _identity(suffix: str) -> ResearchInputIdentity:
    replay = CanonicalDatasetReplay(
        dataset_id=f"ds_runtime_{suffix}",
        event_count=35,
        canonical_event_content_sha256="a" * 64,
        parquet_sha256="c" * 64,
        raw_manifest_id=f"raw_runtime_{suffix}",
        raw_manifest_content_sha256="b" * 64,
    )
    return ResearchInputIdentity.from_verified_replay(
        "us_equities.day_trading.runtime_features",
        replay,
    )


def _decision() -> SubscriptionPolicyDecision:
    scanner_identity = _identity("scanner")
    return build_subscription_policy_decision(
        BroadScannerSnapshot(
            identity=scanner_identity,
            observed_at=_NOW - dt.timedelta(seconds=5),
            candidates=(
                BroadScannerCandidate(
                    instrument_id=_INSTRUMENT_ID,
                    symbol=_SYMBOL,
                    priority_score=Decimal("10"),
                    source_rank=1,
                ),
            ),
        ),
        evaluated_at=_NOW,
        active=(),
        cooldowns=(),
        config=SubscriptionPolicyConfig(
            capacity=1,
            max_candidate_age=dt.timedelta(seconds=30),
            minimum_residency=dt.timedelta(minutes=2),
            eviction_cooldown=dt.timedelta(minutes=5),
        ),
    )


def _bar(index: int) -> CompletedMinuteBar:
    start = dt.datetime(2026, 7, 17, 13, 30, tzinfo=_UTC) + dt.timedelta(minutes=index)
    close = Decimal("100") + Decimal(index) / Decimal(10)
    return CompletedMinuteBar(
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        open=close,
        high=close + Decimal("0.5"),
        low=close - Decimal("0.5"),
        close=close,
        volume=100 + index,
    )


def _batch(
    epoch: str,
    sequences: Sequence[int],
    *,
    identity_suffix: str,
    payload_suffix: str = "",
) -> MarketDataRuntimeBatch:
    receipts = tuple(
        build_market_data_runtime_receipt(
            source_id=_SOURCE_ID,
            connection_epoch=epoch,
            sequence=sequence,
            received_at=_bar(sequence - 1).end_at + dt.timedelta(seconds=1),
            raw_payload=f"bar:{sequence}:{payload_suffix}".encode(),
            instrument_id=_INSTRUMENT_ID,
            symbol=_SYMBOL,
            completed_bar=_bar(sequence - 1),
        )
        for sequence in sequences
    )
    return MarketDataRuntimeBatch(
        source_id=_SOURCE_ID,
        connection_epoch=epoch,
        identity=_identity(identity_suffix),
        receipts=receipts,
    )


def _request() -> tuple[RuntimeFeatureRequest, ...]:
    return (
        RuntimeFeatureRequest(
            instrument_id=_INSTRUMENT_ID,
            expected_cumulative_volume=Decimal("4000"),
        ),
    )


def _store(tmp_path: Path) -> MarketDataRuntimeStore:
    return MarketDataRuntimeStore(tmp_path / "us-market-data-runtime.sqlite3")


def test_normal_cycle_persists_raw_receipts_and_builds_ready_feature(tmp_path: Path) -> None:
    store = _store(tmp_path)
    adapter = _FixtureAdapter((_batch("epoch-1", range(1, 36), identity_suffix="full"),))
    supervisor = UsMarketDataSupervisor(adapter, store, clock=lambda: _NOW)

    result = supervisor.run_cycle(_decision(), _request())

    assert result.status is MarketDataRuntimeStatus.READY
    assert result.inserted_receipt_count == 35
    assert result.duplicate_receipt_count == 0
    assert result.last_sequence == 35
    assert result.feature_snapshots[0].status is FeatureSnapshotStatus.READY
    assert result.feature_snapshots[0].identity == _identity("full")
    assert store.receipt_count(_SOURCE_ID) == 35
    assert store.incidents(_SOURCE_ID) == ()
    assert adapter.calls == [(_decision().desired, None)]


def test_restart_recovers_checkpoint_and_uses_persisted_completed_bars(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_adapter = _FixtureAdapter((_batch("epoch-1", range(1, 21), identity_suffix="partial"),))
    first = UsMarketDataSupervisor(first_adapter, store, clock=lambda: _NOW)

    first_result = first.run_cycle(_decision(), _request())

    second_adapter = _FixtureAdapter((_batch("epoch-1", range(21, 36), identity_suffix="full"),))
    restarted = UsMarketDataSupervisor(second_adapter, store, clock=lambda: _NOW)
    second_result = restarted.run_cycle(_decision(), _request())

    assert first_result.feature_snapshots[0].status is FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY
    assert second_adapter.calls[0][0] == _decision().desired
    assert second_adapter.calls[0][1] is not None
    assert second_adapter.calls[0][1].last_sequence == 20
    assert second_result.status is MarketDataRuntimeStatus.READY
    assert second_result.last_sequence == 35
    assert second_result.feature_snapshots[0].status is FeatureSnapshotStatus.READY
    assert store.receipt_count(_SOURCE_ID) == 35


def test_exact_duplicate_receipt_is_idempotent_and_does_not_republish_feature(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_batch = _batch("epoch-1", range(1, 36), identity_suffix="full")
    adapter = _FixtureAdapter(
        (
            first_batch,
            MarketDataRuntimeBatch(
                source_id=_SOURCE_ID,
                connection_epoch="epoch-1",
                identity=_identity("full"),
                receipts=(first_batch.receipts[-1],),
            ),
        )
    )
    supervisor = UsMarketDataSupervisor(adapter, store, clock=lambda: _NOW)

    _ = supervisor.run_cycle(_decision(), _request())
    duplicate = supervisor.run_cycle(_decision(), _request())

    assert duplicate.status is MarketDataRuntimeStatus.NO_NEW_DATA
    assert duplicate.inserted_receipt_count == 0
    assert duplicate.duplicate_receipt_count == 1
    assert duplicate.feature_snapshots == ()
    assert store.receipt_count(_SOURCE_ID) == 35


def test_sequence_gap_blocks_same_epoch_until_reconnect(tmp_path: Path) -> None:
    store = _store(tmp_path)
    adapter = _FixtureAdapter(
        (
            _batch("epoch-1", (1, 3), identity_suffix="gap"),
            _batch("epoch-1", (4,), identity_suffix="gap-next"),
            _batch("epoch-2", range(1, 36), identity_suffix="recovered"),
        )
    )
    supervisor = UsMarketDataSupervisor(adapter, store, clock=lambda: _NOW)

    gap = supervisor.run_cycle(_decision(), _request())
    receipt_count_after_gap = store.receipt_count(_SOURCE_ID)
    still_blocked = supervisor.run_cycle(_decision(), _request())
    recovered = supervisor.run_cycle(_decision(), _request())

    assert gap.status is MarketDataRuntimeStatus.BLOCKED_SEQUENCE_GAP
    assert gap.feature_snapshots == ()
    assert receipt_count_after_gap == 2
    assert still_blocked.status is MarketDataRuntimeStatus.BLOCKED_SEQUENCE_GAP
    assert still_blocked.feature_snapshots == ()
    assert recovered.status is MarketDataRuntimeStatus.READY
    assert recovered.feature_snapshots[0].status is FeatureSnapshotStatus.READY
    assert tuple(incident.kind for incident in store.incidents(_SOURCE_ID)) == (
        MarketDataRuntimeIncidentKind.SEQUENCE_GAP,
        MarketDataRuntimeIncidentKind.RECONNECT,
    )
    assert adapter.calls[1][1] is not None
    assert adapter.calls[1][1].last_sequence == 3
    assert adapter.calls[2][1] is not None
    assert adapter.calls[2][1].last_sequence == 4


def test_conflicting_duplicate_sequence_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    adapter = _FixtureAdapter(
        (
            _batch("epoch-1", (1,), identity_suffix="first"),
            _batch("epoch-1", (1,), identity_suffix="conflict", payload_suffix="changed"),
        )
    )
    supervisor = UsMarketDataSupervisor(adapter, store, clock=lambda: _NOW)

    _ = supervisor.run_cycle(_decision(), _request())

    with pytest.raises(ValueError, match="market data runtime input is invalid"):
        _ = supervisor.run_cycle(_decision(), _request())
    assert store.receipt_count(_SOURCE_ID) == 1


def test_store_allows_only_one_writer_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with (
        store.writer(),
        pytest.raises(
            MarketDataWriterLeaseUnavailableError,
            match="market data runtime writer is already active",
        ),
        store.writer(),
    ):
        pytest.fail("second writer lease must not open")


def test_blocked_subscription_policy_never_calls_adapter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    adapter = _FixtureAdapter(())
    supervisor = UsMarketDataSupervisor(adapter, store, clock=lambda: _NOW)
    blocked = replace(
        _decision(),
        status=SubscriptionPolicyStatus.BLOCKED_STALE,
        desired=(),
    )

    result = supervisor.run_cycle(blocked, _request())

    assert result.status is MarketDataRuntimeStatus.BLOCKED_SUBSCRIPTION_POLICY
    assert adapter.calls == []
    assert store.receipt_count(_SOURCE_ID) == 0


def test_tampered_receipt_hash_fails_before_raw_append(tmp_path: Path) -> None:
    store = _store(tmp_path)
    valid = _batch("epoch-1", (1,), identity_suffix="tampered")
    tampered = replace(valid.receipts[0], payload_sha256="0" * 64)
    adapter = _FixtureAdapter((replace(valid, receipts=(tampered,)),))
    supervisor = UsMarketDataSupervisor(adapter, store, clock=lambda: _NOW)

    with pytest.raises(ValueError, match="market data runtime input is invalid"):
        _ = supervisor.run_cycle(_decision(), _request())

    assert store.receipt_count(_SOURCE_ID) == 0
