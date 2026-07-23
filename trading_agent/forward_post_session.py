from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from trading_agent.challenger_replay_models import ReplaySourceRejectedError
from trading_agent.challenger_replay_source import load_replay_source
from trading_agent.daily_research_sources import (
    MissingResearchArtifactError,
    load_20bp_metrics,
    load_artifacts,
)
from trading_agent.forward_session_progress import (
    audit_forward_session_progress,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

PostSessionFinalizer = Callable[[Path, dt.datetime], int]
PostSessionRunner = Callable[[Path, dt.datetime], int | None]


class ForwardPostSessionStatus(StrEnum):
    RECOVERED = "recovered"
    REPLAYED = "replayed"


@dataclass(frozen=True, slots=True)
class ForwardPostSessionError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ForwardPostSessionResult:
    status: ForwardPostSessionStatus
    watch_cycles: int
    ranking_cycles: int
    retry_cycles: int
    candidate_input_cycles: int
    candidate_inputs: int
    causal_bars: int
    complete_symbols: int
    completed_trades: int
    artifact_count: int


class _CycleRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    started_at: AwareDatetime
    exit_code: int
    status: Literal["ok", "failed"]

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        expected = "ok" if self.exit_code == 0 else "failed"
        if self.status != expected:
            raise ForwardPostSessionError("cycle_status_mismatch")
        return self


def close_forward_post_session(
    session: Path,
    session_date: dt.date,
    *,
    minimum_watch_cycles: int,
    observed_at: dt.datetime,
    finalizer: PostSessionFinalizer,
    runner: PostSessionRunner,
) -> ForwardPostSessionResult:
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        raise ForwardPostSessionError("not_a_trading_session")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ForwardPostSessionError("regular_session_not_closed")
    observed_new_york = observed_at.astimezone(NEW_YORK)
    if observed_new_york < bounds[1]:
        raise ForwardPostSessionError("regular_session_not_closed")
    try:
        watch_rows = _cycle_rows(session / "watch_cycles.csv")
    except (OSError, ValueError):
        raise ForwardPostSessionError("watch_cycles_unreadable") from None
    if not watch_rows or any(
        row.started_at.astimezone(NEW_YORK).date() != session_date
        or not bounds[0] <= row.started_at.astimezone(NEW_YORK) < bounds[1]
        for row in watch_rows
    ):
        raise ForwardPostSessionError("watch_session_scope_invalid")
    progress = audit_forward_session_progress(
        session,
        minimum_watch_cycles,
    )
    if not progress.clean or progress.quality is None:
        raise ForwardPostSessionError("forward_progress_blocked")
    post_rows = _post_rows(session, session_date, bounds[1])
    if any(row.exit_code != 0 for row in post_rows):
        raise ForwardPostSessionError("post_session_failure_preserved")
    if len(post_rows) > 1:
        raise ForwardPostSessionError("post_session_terminal_cardinality")
    if post_rows:
        return _verified_result(
            ForwardPostSessionStatus.REPLAYED,
            session,
            progress.quality.watch_cycles,
            progress.quality.ranking_cycles,
            progress.quality.read_retry_cycles,
            progress.quality.candidate_input_cycles,
        )
    if observed_new_york.date() != session_date:
        raise ForwardPostSessionError("historical_recovery_forbidden")
    try:
        _ = finalizer(session, observed_at)
    except (OSError, RuntimeError, ValueError):
        raise ForwardPostSessionError(
            "recommendation_finalization_failed"
        ) from None
    try:
        exit_code = runner(session, observed_at)
    except (OSError, RuntimeError, ValueError):
        raise ForwardPostSessionError("post_session_chain_failed") from None
    if exit_code != 0:
        raise ForwardPostSessionError("post_session_chain_failed")
    completed_rows = _post_rows(session, session_date, bounds[1])
    if (
        len(completed_rows) != 1
        or completed_rows[0].exit_code != 0
    ):
        raise ForwardPostSessionError("post_session_terminal_missing")
    return _verified_result(
        ForwardPostSessionStatus.RECOVERED,
        session,
        progress.quality.watch_cycles,
        progress.quality.ranking_cycles,
        progress.quality.read_retry_cycles,
        progress.quality.candidate_input_cycles,
    )


def _post_rows(
    session: Path,
    session_date: dt.date,
    closed_at: dt.datetime,
) -> tuple[_CycleRow, ...]:
    path = session / "post_session_metrics_cycles.csv"
    if not path.is_file():
        return ()
    try:
        rows = _cycle_rows(path)
    except (OSError, ValueError):
        raise ForwardPostSessionError(
            "post_session_terminal_unreadable"
        ) from None
    if any(
        row.started_at.astimezone(NEW_YORK).date() != session_date
        or row.started_at.astimezone(NEW_YORK) < closed_at
        for row in rows
    ):
        raise ForwardPostSessionError("post_session_scope_invalid")
    return rows


def _cycle_rows(path: Path) -> tuple[_CycleRow, ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(
            _CycleRow.model_validate(row)
            for row in csv.DictReader(handle)
        )


def _verified_result(
    status: ForwardPostSessionStatus,
    session: Path,
    watch_cycles: int,
    ranking_cycles: int,
    retry_cycles: int,
    candidate_input_cycles: int,
) -> ForwardPostSessionResult:
    try:
        artifacts = load_artifacts(session)
        metrics = load_20bp_metrics(
            session / "paper_metrics" / "paper_metrics.csv"
        )
        source = load_replay_source(session)
    except (
        MissingResearchArtifactError,
        OSError,
        ReplaySourceRejectedError,
        RuntimeError,
        StopIteration,
        ValueError,
    ):
        raise ForwardPostSessionError(
            "closed_session_verification_failed"
        ) from None
    complete_symbols = sum(row.complete for row in source.coverage)
    if not source.coverage or complete_symbols != len(source.coverage):
        raise ForwardPostSessionError("causal_coverage_incomplete")
    return ForwardPostSessionResult(
        status,
        watch_cycles,
        ranking_cycles,
        retry_cycles,
        candidate_input_cycles,
        len(source.contexts),
        len(source.bars),
        complete_symbols,
        metrics.trade_count,
        len(artifacts),
    )


__all__ = (
    "ForwardPostSessionError",
    "ForwardPostSessionResult",
    "ForwardPostSessionStatus",
    "PostSessionFinalizer",
    "PostSessionRunner",
    "close_forward_post_session",
)
