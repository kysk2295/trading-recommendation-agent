from __future__ import annotations

import csv
import datetime as dt
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from trading_agent.daily_research_models import (
    ArtifactChecksum,
    MetricSnapshot,
    SessionQuality,
)

REQUIRED_ARTIFACTS: Final = (
    "kis_ranking_request_coverage.csv",
    "watch_cycles.csv",
    "kis_ranking_snapshots.csv",
    "market_risk_screen.csv",
    "paper_recommendations.sqlite3",
    "paper_metrics/paper_metrics.csv",
)
OPTIONAL_ARTIFACTS: Final = (
    "kis_read_retry_cycles.csv",
    "kis_read_retry_events.csv",
)


class _CoverageRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: dt.datetime
    status: Literal["ok", "failed"]


class _WatchRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    exit_code: int


class _RetryRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    retry_count: int
    recovered_count: int
    repeated_failure_count: int


class _MetricRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    side_cost_bps: str
    trade_count: str
    win_rate: str
    average_return: str
    profit_factor: str
    cumulative_return: str
    max_drawdown: str
    mean_ci_low: str
    mean_ci_high: str


@dataclass(frozen=True, slots=True)
class MissingResearchArtifactError(RuntimeError):
    path: Path

    def __str__(self) -> str:
        return f"일일 연구 원장 필수 산출물이 없습니다: {self.path}"


def load_artifacts(session: Path) -> tuple[ArtifactChecksum, ...]:
    required = tuple(_checksum(session, relative) for relative in REQUIRED_ARTIFACTS)
    optional = tuple(_checksum(session, relative) for relative in OPTIONAL_ARTIFACTS if (session / relative).is_file())
    return (*required, *optional)


def data_version(artifacts: tuple[ArtifactChecksum, ...]) -> str:
    material = "|".join(f"{row.path}:{row.sha256}:{row.size_bytes}" for row in artifacts)
    return hashlib.sha256(material.encode()).hexdigest()


def load_20bp_metrics(path: Path) -> MetricSnapshot:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(_MetricRow.model_validate(row) for row in csv.DictReader(handle))
    row = next(item for item in rows if item.side_cost_bps == "20")
    return MetricSnapshot(
        side_cost_bps=20,
        trade_count=int(row.trade_count),
        win_rate=_optional_float(row.win_rate),
        average_return=_optional_float(row.average_return),
        profit_factor=_optional_float(row.profit_factor),
        cumulative_return=_optional_float(row.cumulative_return),
        max_drawdown=_optional_float(row.max_drawdown),
        mean_ci_low=_optional_float(row.mean_ci_low),
        mean_ci_high=_optional_float(row.mean_ci_high),
    )


def load_session_quality(
    session: Path,
    completed_trades: int,
) -> tuple[SessionQuality, tuple[str, ...]]:
    coverage = _coverage_rows(session / "kis_ranking_request_coverage.csv")
    watch = _watch_rows(session / "watch_cycles.csv")
    retries = _retry_rows(session / "kis_read_retry_cycles.csv")
    ranking_cycles = len({row.observed_at for row in coverage})
    ranking_failures = sum(row.status == "failed" for row in coverage)
    failed_watch_cycles = sum(row.exit_code != 0 for row in watch)
    read_retries = sum(row.retry_count for row in retries)
    read_retry_recoveries = sum(row.recovered_count for row in retries)
    read_retry_failures = sum(row.repeated_failure_count for row in retries)
    coverage_complete = len(coverage) == ranking_cycles * 6
    retry_coverage_complete = len(retries) == len(watch)
    eligible = (
        bool(watch)
        and ranking_cycles == len(watch)
        and coverage_complete
        and retry_coverage_complete
        and ranking_failures == 0
        and failed_watch_cycles == 0
    )
    incidents: list[str] = []
    if ranking_cycles != len(watch):
        incidents.append(f"coverage_cycle_mismatch:{ranking_cycles}/{len(watch)}")
    if not coverage_complete:
        incidents.append(f"ranking_request_count_mismatch:{len(coverage)}/{ranking_cycles * 6}")
    if ranking_failures:
        incidents.append(f"ranking_request_failures:{ranking_failures}")
    if failed_watch_cycles:
        incidents.append(f"watch_cycle_failures:{failed_watch_cycles}")
    if not retry_coverage_complete:
        incidents.append(f"retry_cycle_mismatch:{len(retries)}/{len(watch)}")
    if read_retries:
        incidents.append(f"kis_read_retries:{read_retries}")
    if read_retry_recoveries:
        incidents.append(f"kis_read_recoveries:{read_retry_recoveries}")
    if read_retry_failures:
        incidents.append(f"kis_read_retry_failures:{read_retry_failures}")
    database = session / "paper_recommendations.sqlite3"
    return (
        SessionQuality(
            forward_day_eligible=eligible,
            ranking_cycles=ranking_cycles,
            ranking_requests=len(coverage),
            ranking_failures=ranking_failures,
            watch_cycles=len(watch),
            failed_watch_cycles=failed_watch_cycles,
            read_retry_cycles=len(retries),
            read_retries=read_retries,
            read_retry_recoveries=read_retry_recoveries,
            read_retry_failures=read_retry_failures,
            archived_bars=_table_count(database, "candidate_minute_bars"),
            recommendations=_table_count(database, "recommendations"),
            completed_trades=completed_trades,
            eligible_completed_trades=completed_trades if eligible else 0,
        ),
        tuple(incidents),
    )


def _coverage_rows(path: Path) -> tuple[_CoverageRow, ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(_CoverageRow.model_validate(row) for row in csv.DictReader(handle))


def _watch_rows(path: Path) -> tuple[_WatchRow, ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(_WatchRow.model_validate(row) for row in csv.DictReader(handle))


def _retry_rows(path: Path) -> tuple[_RetryRow, ...]:
    if not path.is_file():
        return ()
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(_RetryRow.model_validate(row) for row in csv.DictReader(handle))


def _optional_float(value: str) -> float | None:
    return None if not value else float(value)


def _table_count(database: Path, table: str) -> int:
    with sqlite3.connect(database) as connection:
        present: tuple[int] | None = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if present is None or present[0] == 0:
            return 0
        row: tuple[int] | None = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return 0 if row is None else row[0]


def _checksum(session: Path, relative: str) -> ArtifactChecksum:
    path = session / relative
    if not path.is_file():
        raise MissingResearchArtifactError(path)
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return ArtifactChecksum(path=relative, sha256=digest, size_bytes=path.stat().st_size)
