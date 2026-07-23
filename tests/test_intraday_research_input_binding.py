from __future__ import annotations

import datetime as dt
import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from tests.intraday_research_input_binding_fixtures import (
    NOW,
    PRODUCER_COMMIT,
    write_dataset,
    write_entitlement,
    write_queue,
)
from trading_agent.data_foundation_manifest import load_data_foundation_artifact
from trading_agent.intraday_research_input_binding import (
    bind_intraday_research_input,
)
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchInputBindingError,
    IntradayResearchInputBindingRequest,
    IntradayResearchStrategyBinding,
)
from trading_agent.intraday_research_loop_models import load_intraday_research_manifest
from trading_agent.strategy_data_gate import StrategyDataStatus
from trading_agent.strategy_factory import StrategyMode


def test_binding_publishes_ready_foundation_and_exact_v2_manifest(tmp_path: Path) -> None:
    dataset = write_dataset(tmp_path)
    queue_path, card_keys = write_queue(tmp_path)
    entitlement_path = write_entitlement(tmp_path)
    request = IntradayResearchInputBindingRequest(
        dataset_csv=dataset.csv_path,
        dataset_receipt=dataset.receipt_path,
        entitlement_contract=entitlement_path,
        source_queue_artifact=queue_path,
        output_root=tmp_path / "binding",
        strategy_bindings=(
            IntradayResearchStrategyBinding(
                strategy=StrategyMode.VWAP_RECLAIM,
                strategy_version="actual_vwap_reclaim_v1",
                queue_card_key=card_keys[0],
            ),
        ),
        code_version="e" * 40,
        registered_at=NOW,
        observed_at=NOW,
        minimum_training_sessions=0,
        max_bars=500,
        max_sessions=1,
        per_side_fee_bps=5,
        per_side_slippage_bps=15,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
    )

    first = bind_intraday_research_input(request)
    replay = bind_intraday_research_input(request)

    foundation = load_data_foundation_artifact(first.foundation_paths[0])
    manifest = load_intraday_research_manifest(first.manifest_path)
    receipt = json.loads(first.receipt_path.read_text(encoding="utf-8"))
    assert foundation.sha256 == first.foundation_sha256s[0]
    assert foundation.manifest.evaluate_data_readiness().status is StrategyDataStatus.READY
    assert foundation.manifest.capabilities[0].historical_from == dt.date(2026, 7, 14)
    assert foundation.manifest.entitlements[0].entitlement_id == (
        "kis-us-candidate-minute-historical-research-v1"
    )
    assert manifest.input_sha256 == dataset.input_sha256
    assert manifest.source_queue_snapshot_id is not None
    assert manifest.hypotheses[0].data_foundation_sha256 == foundation.sha256
    assert receipt["input_sha256"] == dataset.input_sha256
    assert receipt["dataset_producer_commit_sha"] == PRODUCER_COMMIT
    assert receipt["foundation_sha256s"] == [foundation.sha256]
    assert receipt["manifest_sha256"] == first.manifest_sha256
    assert first.created is True
    assert replay.created is False
    for path in (*first.foundation_paths, first.manifest_path, first.receipt_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_binding_rejects_dataset_or_entitlement_not_bound_to_actual_source(tmp_path: Path) -> None:
    dataset = write_dataset(tmp_path)
    queue_path, card_keys = write_queue(tmp_path)
    entitlement_path = write_entitlement(tmp_path, provider="fixture")
    request = _request(
        tmp_path,
        dataset.csv_path,
        dataset.receipt_path,
        entitlement_path,
        queue_path,
        card_keys[0],
    )

    with pytest.raises(IntradayResearchInputBindingError, match="input binding blocked"):
        _ = bind_intraday_research_input(request)

    assert not (tmp_path / "binding").exists()


def test_binding_supports_three_independent_strategy_cards(tmp_path: Path) -> None:
    dataset = write_dataset(tmp_path)
    cards = (
        ("H-MOM-VWAP-ACTUAL-001", "a" * 64),
        ("H-MOM-HOD-ACTUAL-001", "b" * 64),
        ("H-MOM-GAP-ACTUAL-001", "c" * 64),
    )
    queue_path, card_keys = write_queue(tmp_path, cards)
    entitlement_path = write_entitlement(tmp_path)
    strategies = (
        StrategyMode.VWAP_RECLAIM,
        StrategyMode.HOD_BREAKOUT,
        StrategyMode.GAP_AND_GO,
    )
    request = IntradayResearchInputBindingRequest(
        dataset_csv=dataset.csv_path,
        dataset_receipt=dataset.receipt_path,
        entitlement_contract=entitlement_path,
        source_queue_artifact=queue_path,
        output_root=tmp_path / "binding",
        strategy_bindings=tuple(
            IntradayResearchStrategyBinding(
                strategy=strategy,
                strategy_version=f"actual_{strategy.value}_v1",
                queue_card_key=card_key,
            )
            for strategy, card_key in zip(strategies, card_keys, strict=True)
        ),
        code_version="e" * 40,
        registered_at=NOW,
        observed_at=NOW,
        minimum_training_sessions=0,
        max_bars=500,
        max_sessions=1,
        per_side_fee_bps=5,
        per_side_slippage_bps=15,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
    )

    result = bind_intraday_research_input(request)
    manifest = load_intraday_research_manifest(result.manifest_path)

    assert len(result.foundation_paths) == 3
    assert len(set(result.foundation_sha256s)) == 3
    assert manifest.strategies == strategies
    assert tuple(item.queue_card_key for item in manifest.hypotheses) == card_keys


def test_binding_rejects_registration_after_observation_time(tmp_path: Path) -> None:
    dataset = write_dataset(tmp_path)
    queue_path, card_keys = write_queue(tmp_path)
    entitlement_path = write_entitlement(tmp_path)
    request = _request(
        tmp_path,
        dataset.csv_path,
        dataset.receipt_path,
        entitlement_path,
        queue_path,
        card_keys[0],
    )

    future = replace(request, observed_at=NOW - dt.timedelta(seconds=1))

    with pytest.raises(IntradayResearchInputBindingError, match="input binding blocked"):
        _ = bind_intraday_research_input(future)
    assert not (tmp_path / "binding").exists()


def _request(
    tmp_path: Path,
    csv_path: Path,
    receipt_path: Path,
    entitlement_path: Path,
    queue_path: Path,
    card_key: str,
) -> IntradayResearchInputBindingRequest:
    return IntradayResearchInputBindingRequest(
        dataset_csv=csv_path,
        dataset_receipt=receipt_path,
        entitlement_contract=entitlement_path,
        source_queue_artifact=queue_path,
        output_root=tmp_path / "binding",
        strategy_bindings=(
            IntradayResearchStrategyBinding(
                strategy=StrategyMode.VWAP_RECLAIM,
                strategy_version="actual_vwap_reclaim_v1",
                queue_card_key=card_key,
            ),
        ),
        code_version="e" * 40,
        registered_at=NOW,
        observed_at=NOW,
        minimum_training_sessions=0,
        max_bars=500,
        max_sessions=1,
        per_side_fee_bps=5,
        per_side_slippage_bps=15,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
    )
