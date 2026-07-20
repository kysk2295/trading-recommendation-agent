from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_us_news_catalyst_shadow_trial import (
    _observation_manifest,
    _registration_request,
)
from tests.us_news_catalyst_trial_fixtures import (
    OBSERVED,
    SESSION_DATE,
    STRATEGY_VERSION,
    projected_evidence,
    registered_ledger,
)
from trading_agent.us_news_catalyst_reviewer import (
    UsNewsCatalystReviewerAction,
    review_us_news_catalyst_trials,
    us_news_catalyst_reviewer_action,
)
from trading_agent.us_news_catalyst_trial import (
    finalize_us_news_catalyst_trial,
    register_us_news_catalyst_daily_trial,
    start_us_news_catalyst_trial,
)
from trading_agent.us_news_catalyst_trial_artifact import (
    publish_us_news_catalyst_setup_observation_manifest,
)


def test_independent_reviewer_replays_completed_trial_and_continues_collection(
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
    observations, _ = publish_us_news_catalyst_setup_observation_manifest(
        tmp_path / "observations",
        _observation_manifest(started.cohort.artifact_id, registered.registration.trial_id),
    )
    _ = finalize_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        started.cohort,
        observations,
        artifacts,
        finalized_at=OBSERVED + dt.timedelta(minutes=31),
    )

    first = review_us_news_catalyst_trials(
        ledger,
        artifacts,
        tmp_path / "reviews",
        strategy_version=STRATEGY_VERSION,
        as_of_session=SESSION_DATE,
        reviewed_at=dt.datetime(2026, 7, 21, 20, 1, tzinfo=dt.UTC),
    )
    second = review_us_news_catalyst_trials(
        ledger,
        artifacts,
        tmp_path / "reviews",
        strategy_version=STRATEGY_VERSION,
        as_of_session=SESSION_DATE,
        reviewed_at=dt.datetime(2026, 7, 21, 20, 2, tzinfo=dt.UTC),
    )

    assert first.created is True
    assert second.created is False
    assert first.artifact == second.artifact
    assert first.artifact.payload.action is UsNewsCatalystReviewerAction.CONTINUE_COLLECTION
    assert first.artifact.payload.completed_session_count == 1
    assert first.artifact.payload.automatic_state_change_allowed is False
    assert first.artifact.payload.order_authority_change_allowed is False
    assert first.artifact.payload.allocation_change_allowed is False


def test_reviewer_action_requires_mature_balanced_sample_and_clean_quality() -> None:
    assert (
        us_news_catalyst_reviewer_action(
            completed_sessions=20,
            treatment_observations=100,
            control_observations=100,
            data_quality_issue_count=0,
        )
        is UsNewsCatalystReviewerAction.COMPARISON_READY
    )
    assert (
        us_news_catalyst_reviewer_action(
            completed_sessions=20,
            treatment_observations=100,
            control_observations=100,
            data_quality_issue_count=1,
        )
        is UsNewsCatalystReviewerAction.DATA_QUALITY_REVIEW
    )


def test_reviewer_routes_missing_terminal_setup_manifest_to_data_quality_review(
    tmp_path: Path,
) -> None:
    ledger, artifacts = _completed_trial(tmp_path)
    next(artifacts.glob("us_news_catalyst_setup_*.json")).unlink()

    result = review_us_news_catalyst_trials(
        ledger,
        artifacts,
        tmp_path / "reviews",
        strategy_version=STRATEGY_VERSION,
        as_of_session=SESSION_DATE,
        reviewed_at=dt.datetime(2026, 7, 21, 20, 1, tzinfo=dt.UTC),
    )

    assert result.artifact.payload.action is UsNewsCatalystReviewerAction.DATA_QUALITY_REVIEW
    assert result.artifact.payload.failed_session_count == 1


def test_reviewer_routes_censored_session_to_data_quality_review(tmp_path: Path) -> None:
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
    _ = finalize_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        started.cohort,
        None,
        artifacts,
        finalized_at=OBSERVED + dt.timedelta(minutes=31),
    )

    result = review_us_news_catalyst_trials(
        ledger,
        artifacts,
        tmp_path / "reviews",
        strategy_version=STRATEGY_VERSION,
        as_of_session=SESSION_DATE,
        reviewed_at=dt.datetime(2026, 7, 21, 20, 1, tzinfo=dt.UTC),
    )

    assert result.artifact.payload.action is UsNewsCatalystReviewerAction.DATA_QUALITY_REVIEW
    assert result.artifact.payload.censored_session_count == 1


def _completed_trial(tmp_path: Path):
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
    observations, _ = publish_us_news_catalyst_setup_observation_manifest(
        tmp_path / "observations",
        _observation_manifest(started.cohort.artifact_id, registered.registration.trial_id),
    )
    _ = finalize_us_news_catalyst_trial(
        ledger,
        registered.registration.trial_id,
        started.cohort,
        observations,
        artifacts,
        finalized_at=OBSERVED + dt.timedelta(minutes=31),
    )
    return ledger, artifacts
