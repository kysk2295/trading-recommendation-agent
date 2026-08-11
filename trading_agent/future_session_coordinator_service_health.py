from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    FutureSessionCoordinatorServiceReport,
    FutureSessionCoordinatorServiceState,
    canonical_service_config_sha256,
    canonical_service_report_json,
)
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)

type CoordinatorClock = Callable[[], dt.datetime]
type CoordinatorHealthEvaluator = Callable[
    [FutureSessionCoordinatorServiceConfig, dt.datetime, dt.datetime],
    "FutureSessionCoordinatorHealthEvaluation",
]
type CoordinatorSleeper = Callable[[float], None]


class FutureSessionCoordinatorHealthEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    accepted: bool
    reason: Literal[
        "fresh_matching_ready",
        "report_missing_or_invalid",
        "config_mismatch",
        "sha_mismatch",
        "not_fresh",
        "observed_in_future",
        "runtime_failed",
    ]
    report: FutureSessionCoordinatorServiceReport | None


def read_persisted_coordinator_report(
    config: FutureSessionCoordinatorServiceConfig,
) -> FutureSessionCoordinatorServiceReport:
    payload = read_private_text_query_only(config.state_root / "future-session-coordinator-status.json")
    report = FutureSessionCoordinatorServiceReport.model_validate_json(payload)
    if canonical_service_report_json(report) != payload:
        raise ValueError("invalid coordinator report")
    return report


def evaluate_persisted_coordinator_health(
    config: FutureSessionCoordinatorServiceConfig,
    started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> FutureSessionCoordinatorHealthEvaluation:
    try:
        report = read_persisted_coordinator_report(config)
    except (InvalidPrivateQueryFileError, OSError, TypeError, ValueError):
        return _rejected("report_missing_or_invalid")
    if report.config_sha256 != canonical_service_config_sha256(config):
        return _rejected("config_mismatch", report)
    if report.scheduler_main_sha != config.scheduler_main_sha:
        return _rejected("sha_mismatch", report)
    if report.service_started_at <= started_at or report.observed_at <= started_at:
        return _rejected("not_fresh", report)
    if report.service_started_at > evaluated_at or report.observed_at > evaluated_at:
        return _rejected("observed_in_future", report)
    if report.service_state is FutureSessionCoordinatorServiceState.FAILED:
        return _rejected("runtime_failed", report)
    return FutureSessionCoordinatorHealthEvaluation(
        accepted=True,
        reason="fresh_matching_ready",
        report=report,
    )


def await_fresh_coordinator_health(
    config: FutureSessionCoordinatorServiceConfig,
    started_at: dt.datetime,
    clock: CoordinatorClock,
    evaluator: CoordinatorHealthEvaluator,
    sleeper: CoordinatorSleeper = time.sleep,
) -> FutureSessionCoordinatorHealthEvaluation:
    evaluation = evaluator(config, started_at, clock())
    for _ in range(20):
        if evaluation.accepted:
            return evaluation
        sleeper(0.25)
        evaluation = evaluator(config, started_at, clock())
    return evaluation


def evaluate_current_coordinator_health(
    config: FutureSessionCoordinatorServiceConfig,
    evaluated_at: dt.datetime,
) -> FutureSessionCoordinatorHealthEvaluation:
    maximum_age = dt.timedelta(seconds=max(config.poll_interval_seconds * 2, 5))
    evaluation = evaluate_persisted_coordinator_health(
        config,
        dt.datetime.min.replace(tzinfo=dt.UTC),
        evaluated_at,
    )
    if (
        evaluation.accepted
        and evaluation.report is not None
        and evaluation.report.observed_at <= evaluated_at - maximum_age
    ):
        return _rejected("not_fresh", evaluation.report)
    return evaluation


def _rejected(
    reason: Literal[
        "report_missing_or_invalid",
        "config_mismatch",
        "sha_mismatch",
        "not_fresh",
        "observed_in_future",
        "runtime_failed",
    ],
    report: FutureSessionCoordinatorServiceReport | None = None,
) -> FutureSessionCoordinatorHealthEvaluation:
    return FutureSessionCoordinatorHealthEvaluation(
        accepted=False,
        reason=reason,
        report=report,
    )


__all__ = (
    "CoordinatorClock",
    "CoordinatorHealthEvaluator",
    "CoordinatorSleeper",
    "FutureSessionCoordinatorHealthEvaluation",
    "await_fresh_coordinator_health",
    "evaluate_current_coordinator_health",
    "evaluate_persisted_coordinator_health",
    "read_persisted_coordinator_report",
)
