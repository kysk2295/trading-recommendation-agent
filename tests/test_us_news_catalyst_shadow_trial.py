from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests.us_news_catalyst_trial_fixtures import (
    CODE_VERSION,
    OBSERVED,
    SESSION_DATE,
    STRATEGY_VERSION,
    projected_evidence,
    registered_ledger,
)
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.us_news_catalyst_trial import (
    InvalidUsNewsCatalystTrialError,
    finalize_us_news_catalyst_trial,
    register_us_news_catalyst_daily_trial,
    start_us_news_catalyst_trial,
)
from trading_agent.us_news_catalyst_trial_artifact import (
    publish_us_news_catalyst_setup_observation_manifest,
)
from trading_agent.us_news_catalyst_trial_models import (
    UsNewsCatalystDailyTrialRegistrationRequest,
)
from trading_agent.us_news_catalyst_trial_outcome_models import (
    UsNewsCatalystSetupFeatureObservation,
    create_us_news_catalyst_setup_observation_manifest,
)


def test_daily_trial_registers_preopen_and_replays_exactly(tmp_path: Path) -> None:
    ledger = registered_ledger(tmp_path)
    request = _registration_request()

    first = register_us_news_catalyst_daily_trial(ledger, request)
    second = register_us_news_catalyst_daily_trial(ledger, request)

    assert first.created is True
    assert second.created is False
    assert first.registration == second.registration
    assert first.registration.planned_start == SESSION_DATE
    assert "zero_news_equal_count_v1" in first.registration.evidence_budget


def test_new_daily_trial_registration_after_open_is_rejected(tmp_path: Path) -> None:
    ledger = registered_ledger(tmp_path)
    request = _registration_request(
        registered_at=dt.datetime(2026, 7, 21, 14, tzinfo=dt.UTC)
    )

    with pytest.raises(InvalidUsNewsCatalystTrialError):
        _ = register_us_news_catalyst_daily_trial(ledger, request)


def test_started_trial_freezes_ranked_treatment_and_equal_zero_news_control(
    tmp_path: Path,
) -> None:
    ledger = registered_ledger(tmp_path)
    registered = register_us_news_catalyst_daily_trial(ledger, _registration_request())
    projection, evidence = projected_evidence(ledger)

    first = start_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        projection,
        evidence,
        tmp_path / "artifacts",
        started_at=OBSERVED + dt.timedelta(seconds=1),
    )
    second = start_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        projection,
        evidence,
        tmp_path / "artifacts",
        started_at=OBSERVED + dt.timedelta(seconds=2),
    )

    assert first.event_created is True
    assert second.event_created is False
    assert first.cohort == second.cohort
    assert first.cohort.payload.treatment_symbols == ("AAPL", "MSFT")
    assert set(first.cohort.payload.control_symbols) == {"NVDA", "TSLA"}
    assert len(ledger.multi_market_trial_events(registered.registration.trial_id)) == 1


def test_complete_setup_observations_finalize_treatment_control_lift(
    tmp_path: Path,
) -> None:
    ledger = registered_ledger(tmp_path)
    registered = register_us_news_catalyst_daily_trial(ledger, _registration_request())
    projection, evidence = projected_evidence(ledger)
    started = start_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        projection,
        evidence,
        tmp_path / "artifacts",
        started_at=OBSERVED + dt.timedelta(seconds=1),
    )
    observations = _observation_manifest(started.cohort.artifact_id, registered.registration.trial_id)
    observation_path, _ = publish_us_news_catalyst_setup_observation_manifest(
        tmp_path / "observations",
        observations,
    )

    first = finalize_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        started.cohort,
        observation_path,
        tmp_path / "artifacts",
        finalized_at=OBSERVED + dt.timedelta(minutes=31),
    )
    second = finalize_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        started.cohort,
        observation_path,
        tmp_path / "artifacts",
        finalized_at=OBSERVED + dt.timedelta(minutes=32),
    )

    assert first.event_created is True
    assert second.event_created is False
    assert first.outcome == second.outcome
    assert first.outcome.payload.terminal_kind is TrialEventKind.COMPLETED
    assert first.outcome.payload.treatment_confirmed_count == 2
    assert first.outcome.payload.control_confirmed_count == 0
    assert first.outcome.payload.confirmation_lift_bps == 10_000


def test_insufficient_zero_news_control_is_censored_not_zero_return(
    tmp_path: Path,
) -> None:
    ledger = registered_ledger(tmp_path)
    registered = register_us_news_catalyst_daily_trial(ledger, _registration_request())
    projection, evidence = projected_evidence(ledger, zero_news_symbols=("TSLA",))
    started = start_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        projection,
        evidence,
        tmp_path / "artifacts",
        started_at=OBSERVED + dt.timedelta(seconds=1),
    )

    result = finalize_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        started.cohort,
        None,
        tmp_path / "artifacts",
        finalized_at=OBSERVED + dt.timedelta(minutes=31),
    )

    assert result.outcome.payload.terminal_kind is TrialEventKind.CENSORED
    assert result.outcome.payload.reason_codes == ("insufficient_zero_news_control",)
    assert result.outcome.payload.confirmation_lift_bps is None


def test_finalize_fails_closed_when_frozen_cohort_artifact_is_missing(
    tmp_path: Path,
) -> None:
    ledger = registered_ledger(tmp_path)
    registered = register_us_news_catalyst_daily_trial(ledger, _registration_request())
    projection, evidence = projected_evidence(ledger)
    artifacts = tmp_path / "artifacts"
    started = start_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        projection,
        evidence,
        artifacts,
        started_at=OBSERVED + dt.timedelta(seconds=1),
    )
    next(artifacts.glob("us_news_catalyst_cohort_*.json")).unlink()

    with pytest.raises(InvalidUsNewsCatalystTrialError):
        _ = finalize_us_news_catalyst_trial(
            ledger,
            registered.registration.trial_id,
            started.cohort,
            None,
            artifacts,
            finalized_at=OBSERVED + dt.timedelta(minutes=31),
        )


def _registration_request(
    *,
    registered_at: dt.datetime = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
) -> UsNewsCatalystDailyTrialRegistrationRequest:
    return UsNewsCatalystDailyTrialRegistrationRequest(
        strategy_version=STRATEGY_VERSION,
        code_version=CODE_VERSION,
        session_date=SESSION_DATE,
        registered_at=registered_at,
    )


def _observation_manifest(
    cohort_id: str,
    trial_id: str,
):
    observed_at = OBSERVED + dt.timedelta(minutes=30)
    observations = tuple(
        UsNewsCatalystSetupFeatureObservation(
            symbol=symbol,
            feature_evidence_id=f"{index:x}" * 64,
            observed_at=observed_at,
            close=Decimal("11") if symbol in {"AAPL", "MSFT"} else Decimal("9"),
            vwap=Decimal("10"),
            rvol=Decimal("2") if symbol in {"AAPL", "MSFT"} else Decimal("1"),
            breakout_close_above_prior_high=symbol in {"AAPL", "MSFT"},
        )
        for index, symbol in enumerate(("AAPL", "MSFT", "NVDA", "TSLA"), start=1)
    )
    return create_us_news_catalyst_setup_observation_manifest(
        trial_id=trial_id,
        cohort_artifact_id=cohort_id,
        evaluator_version="us_news_setup_confirmation_v1",
        observations=observations,
    )
