from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.us_news_catalyst_trial_fixtures import (
    OBSERVED,
    SESSION_DATE,
    projected_evidence,
    registered_ledger,
)
from tests.us_volume_profile_fixtures import volume_profile
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.intraday_feature_kernel import CompletedMinuteBar, build_intraday_feature_snapshot
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_feature_evidence_models import UsFeatureEvidenceBinding
from trading_agent.us_news_catalyst_feature_artifact import (
    feature_artifacts_in,
    publish_us_news_catalyst_feature_artifact,
)
from trading_agent.us_news_catalyst_feature_projection import (
    InvalidUsNewsCatalystFeatureProjectionError,
    project_us_news_catalyst_feature_artifact,
    project_us_news_catalyst_setup_observations,
)
from trading_agent.us_news_catalyst_trial import (
    register_us_news_catalyst_daily_trial,
    start_us_news_catalyst_trial,
)
from trading_agent.us_news_catalyst_trial_models import UsNewsCatalystDailyTrialRegistrationRequest

SETUP_AT = OBSERVED + dt.timedelta(minutes=31, seconds=30)
SYMBOLS = ("AAPL", "MSFT", "NVDA", "TSLA")


def test_runtime_binding_projects_private_immutable_feature_artifact(tmp_path: Path) -> None:
    artifact = project_us_news_catalyst_feature_artifact(_binding("AAPL", 1))

    path, created = publish_us_news_catalyst_feature_artifact(tmp_path / "features", artifact)
    replayed_path, replayed = publish_us_news_catalyst_feature_artifact(
        tmp_path / "features",
        artifact,
    )

    assert created is True
    assert replayed is False
    assert replayed_path == path
    assert path.stat().st_mode & 0o777 == 0o600
    assert feature_artifacts_in(tmp_path / "features") == (artifact,)
    assert artifact.payload.close == Decimal("110")


def test_blocked_runtime_feature_cannot_be_published() -> None:
    binding = _binding("AAPL", 1)
    blocked = replace(binding.snapshot, close=None)

    with pytest.raises(InvalidUsNewsCatalystFeatureProjectionError):
        _ = project_us_news_catalyst_feature_artifact(
            UsFeatureEvidenceBinding(binding.symbol, blocked)
        )


def test_latest_complete_same_cycle_projects_all_cohort_observations(tmp_path: Path) -> None:
    cohort = _cohort(tmp_path)
    older = tuple(
        project_us_news_catalyst_feature_artifact(
            _binding(symbol, index, observed_at=SETUP_AT - dt.timedelta(minutes=1))
        )
        for index, symbol in enumerate(SYMBOLS, start=1)
    )
    current = tuple(
        project_us_news_catalyst_feature_artifact(_binding(symbol, index))
        for index, symbol in enumerate(SYMBOLS, start=1)
    )

    manifest = project_us_news_catalyst_setup_observations(
        cohort,
        (*older, *current),
        evaluated_at=SETUP_AT + dt.timedelta(seconds=30),
    )

    assert tuple(item.symbol for item in manifest.observations) == SYMBOLS
    assert {item.observed_at for item in manifest.observations} == {SETUP_AT}
    assert tuple(item.setup_confirmed for item in manifest.observations) == (
        True,
        True,
        False,
        False,
    )


def test_missing_control_feature_blocks_partial_observation_manifest(tmp_path: Path) -> None:
    cohort = _cohort(tmp_path)
    artifacts = tuple(
        project_us_news_catalyst_feature_artifact(_binding(symbol, index))
        for index, symbol in enumerate(SYMBOLS[:-1], start=1)
    )

    with pytest.raises(InvalidUsNewsCatalystFeatureProjectionError):
        _ = project_us_news_catalyst_setup_observations(
            cohort,
            artifacts,
            evaluated_at=SETUP_AT + dt.timedelta(seconds=30),
        )


def test_stale_complete_cycle_blocks_observation_manifest(tmp_path: Path) -> None:
    cohort = _cohort(tmp_path)
    artifacts = tuple(
        project_us_news_catalyst_feature_artifact(_binding(symbol, index))
        for index, symbol in enumerate(SYMBOLS, start=1)
    )

    with pytest.raises(InvalidUsNewsCatalystFeatureProjectionError):
        _ = project_us_news_catalyst_setup_observations(
            cohort,
            artifacts,
            evaluated_at=SETUP_AT + dt.timedelta(minutes=2, microseconds=1),
        )


def _cohort(tmp_path: Path):
    ledger = registered_ledger(tmp_path)
    request = UsNewsCatalystDailyTrialRegistrationRequest(
        strategy_version=ledger.multi_market_strategy_versions()[0].registration.strategy_version,
        code_version="us-news-catalyst-baseline-fixture-v1",
        session_date=SESSION_DATE,
        registered_at=dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
    )
    registered = register_us_news_catalyst_daily_trial(ledger, request)
    projection, evidence = projected_evidence(ledger)
    return start_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        projection,
        evidence,
        tmp_path / "trial-artifacts",
        started_at=OBSERVED + dt.timedelta(seconds=1),
    ).cohort


def _binding(
    symbol: str,
    index: int,
    *,
    observed_at: dt.datetime = SETUP_AT,
) -> UsFeatureEvidenceBinding:
    instrument_id = f"alpaca:fixture-{symbol.lower()}"
    start = observed_at - dt.timedelta(minutes=60, seconds=30)
    confirmed = symbol in {"AAPL", "MSFT"}
    bars = tuple(
        CompletedMinuteBar(
            start_at=start + dt.timedelta(minutes=minute),
            end_at=start + dt.timedelta(minutes=minute + 1),
            open=Decimal("100"),
            high=Decimal("101") if minute < 59 else Decimal("111"),
            low=Decimal("99") if confirmed else Decimal("98"),
            close=(Decimal("110") if confirmed else Decimal("99")) if minute == 59 else Decimal("100"),
            volume=100,
        )
        for minute in range(60)
    )
    snapshot = build_intraday_feature_snapshot(
        _identity(index),
        instrument_id,
        observed_at,
        bars,
        volume_profile(
            instrument_id,
            SESSION_DATE,
            through_minute=60,
            expected_cumulative_volume=3_000 if confirmed else 6_000,
        ),
    )
    return UsFeatureEvidenceBinding(symbol, snapshot)


def _identity(index: int) -> ResearchInputIdentity:
    replay = CanonicalDatasetReplay(
        dataset_id=f"news-catalyst-feature-{index}",
        event_count=60,
        canonical_event_content_sha256=f"{index:x}" * 64,
        parquet_sha256="a" * 64,
        raw_manifest_id=f"news-catalyst-feature-raw-{index}",
        raw_manifest_content_sha256="b" * 64,
    )
    return ResearchInputIdentity.from_verified_replay(
        "us_equities.day_trading.runtime_features",
        replay,
    )
