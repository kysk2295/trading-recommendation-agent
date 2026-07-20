from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_news_catalyst_reviewer_artifact import (
    publish_us_news_catalyst_review,
    reviews_in,
)
from trading_agent.us_news_catalyst_reviewer_models import (
    US_NEWS_CATALYST_REVIEWER_VERSION,
    InvalidUsNewsCatalystReviewerModelError,
    UsNewsCatalystReviewArtifact,
    UsNewsCatalystReviewerAction,
    UsNewsCatalystReviewPayload,
    review_artifact,
)
from trading_agent.us_news_catalyst_reviewer_replay import (
    UsNewsCatalystTrialAggregate,
    aggregate_us_news_catalyst_trials,
)
from trading_agent.us_news_catalyst_trial_artifact import (
    cohorts_in,
    outcomes_in,
    setup_manifests_in,
)
from trading_agent.us_news_catalyst_trial_outcome_models import US_NEWS_CATALYST_EVALUATOR_VERSION


class InvalidUsNewsCatalystReviewerError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst independent Reviewer input is invalid"


@dataclass(frozen=True, slots=True)
class UsNewsCatalystReviewResult:
    created: bool
    artifact: UsNewsCatalystReviewArtifact


def review_us_news_catalyst_trials(
    ledger: ExperimentLedgerReader,
    artifact_root: Path,
    review_root: Path,
    *,
    strategy_version: str,
    as_of_session: dt.date,
    reviewed_at: dt.datetime,
) -> UsNewsCatalystReviewResult:
    try:
        bounds = regular_session_bounds(as_of_session)
        if bounds is None or reviewed_at < bounds[1]:
            raise InvalidUsNewsCatalystReviewerError
        trials = tuple(
            sorted(
                (
                    item.registration
                    for item in ledger.multi_market_trials()
                    if item.registration.strategy_version == strategy_version
                    and item.registration.planned_end <= as_of_session
                    and item.registration.evaluator_version == US_NEWS_CATALYST_EVALUATOR_VERSION
                ),
                key=lambda item: (item.planned_start, item.trial_id),
            )
        )
        if not trials:
            raise InvalidUsNewsCatalystReviewerError
        aggregate = aggregate_us_news_catalyst_trials(
            ledger,
            trials,
            cohorts_in(artifact_root),
            setup_manifests_in(artifact_root),
            outcomes_in(artifact_root),
        )
        existing = _existing_review(review_root, strategy_version, as_of_session)
        effective_time = reviewed_at if existing is None else existing.payload.reviewed_at
        artifact = _review_artifact(strategy_version, as_of_session, effective_time, trials, aggregate)
        if existing is not None and existing != artifact:
            raise InvalidUsNewsCatalystReviewerError
        _, created = publish_us_news_catalyst_review(review_root, artifact)
        return UsNewsCatalystReviewResult(created, artifact)
    except (AttributeError, InvalidUsNewsCatalystReviewerModelError, OSError, ValueError):
        raise InvalidUsNewsCatalystReviewerError from None


def us_news_catalyst_reviewer_action(
    *,
    completed_sessions: int,
    treatment_observations: int,
    control_observations: int,
    data_quality_issue_count: int,
) -> UsNewsCatalystReviewerAction:
    if min(completed_sessions, treatment_observations, control_observations, data_quality_issue_count) < 0:
        raise InvalidUsNewsCatalystReviewerError
    if data_quality_issue_count > 0:
        return UsNewsCatalystReviewerAction.DATA_QUALITY_REVIEW
    if completed_sessions >= 20 and treatment_observations >= 100 and control_observations >= 100:
        return UsNewsCatalystReviewerAction.COMPARISON_READY
    return UsNewsCatalystReviewerAction.CONTINUE_COLLECTION


def _review_artifact(
    strategy_version: str,
    as_of_session: dt.date,
    reviewed_at: dt.datetime,
    trials: tuple[MultiMarketExperimentTrialRegistration, ...],
    aggregate: UsNewsCatalystTrialAggregate,
) -> UsNewsCatalystReviewArtifact:
    issues = aggregate.censored + aggregate.failed + aggregate.missing
    action = us_news_catalyst_reviewer_action(
        completed_sessions=aggregate.completed,
        treatment_observations=aggregate.treatment_count,
        control_observations=aggregate.control_count,
        data_quality_issue_count=issues,
    )
    reasons = _reasons(action, aggregate)
    treatment_bps = (
        None
        if aggregate.treatment_count == 0
        else aggregate.treatment_confirmed * 10_000 // aggregate.treatment_count
    )
    control_bps = (
        None
        if aggregate.control_count == 0
        else aggregate.control_confirmed * 10_000 // aggregate.control_count
    )
    return review_artifact(UsNewsCatalystReviewPayload(
        strategy_version=strategy_version,
        as_of_session=as_of_session,
        reviewer_version=US_NEWS_CATALYST_REVIEWER_VERSION,
        reviewed_at=reviewed_at,
        included_trial_ids=tuple(sorted(item.trial_id for item in trials)),
        completed_session_count=aggregate.completed,
        censored_session_count=aggregate.censored,
        failed_session_count=aggregate.failed,
        missing_terminal_count=aggregate.missing,
        treatment_observation_count=aggregate.treatment_count,
        control_observation_count=aggregate.control_count,
        treatment_confirmed_count=aggregate.treatment_confirmed,
        control_confirmed_count=aggregate.control_confirmed,
        treatment_confirmation_bps=treatment_bps,
        control_confirmation_bps=control_bps,
        confirmation_lift_bps=(None if treatment_bps is None else treatment_bps - (control_bps or 0)),
        action=action,
        reason_codes=reasons,
    ))


def _reasons(
    action: UsNewsCatalystReviewerAction,
    aggregate: UsNewsCatalystTrialAggregate,
) -> tuple[str, ...]:
    values: list[str] = []
    if aggregate.censored + aggregate.failed + aggregate.missing:
        values.append("terminal_data_quality_issue")
    if aggregate.completed < 20:
        values.append("minimum_20_sessions_not_met")
    if aggregate.treatment_count < 100 or aggregate.control_count < 100:
        values.append("minimum_100_per_arm_not_met")
    if action is UsNewsCatalystReviewerAction.COMPARISON_READY:
        values.append("mature_comparison_ready")
    return tuple(sorted(values))


def _existing_review(
    root: Path,
    strategy_version: str,
    as_of_session: dt.date,
) -> UsNewsCatalystReviewArtifact | None:
    matches = tuple(
        item
        for item in reviews_in(root)
        if item.payload.strategy_version == strategy_version
        and item.payload.as_of_session == as_of_session
        and item.payload.reviewer_version == US_NEWS_CATALYST_REVIEWER_VERSION
    )
    if len(matches) > 1:
        raise InvalidUsNewsCatalystReviewerError
    return None if not matches else matches[0]
__all__ = (
    "InvalidUsNewsCatalystReviewerError",
    "UsNewsCatalystReviewResult",
    "UsNewsCatalystReviewerAction",
    "review_us_news_catalyst_trials",
    "us_news_catalyst_reviewer_action",
)
