from __future__ import annotations

import csv
import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.adaptive_evaluation_models import EvaluatedSession, EvaluationContext
from trading_agent.daily_research_ledger import read_daily_ledger
from trading_agent.daily_research_models import DailyResearchRecord
from trading_agent.kis_live import regular_session_bounds
from trading_agent.metrics import PaperTrade
from trading_agent.models import RecommendationState
from trading_agent.trade_cohort_models import TradeFeatureSource
from trading_agent.trade_cohort_source import TradeCohortSourceError, load_trade_feature_assignments

TRADE_ARTIFACT = "paper_metrics/paper_trades.csv"
REGIME_ARTIFACT = "research_regime_snapshot.json"
DATABASE_ARTIFACT = "paper_recommendations.sqlite3"
RISK_ARTIFACT = "market_risk_screen.csv"
GAP_ARTIFACT = "kis_opening_gap_snapshots.csv"
RegimeLabel = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]


class _TradeRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    recommendation_id: str
    symbol: str
    strategy: str
    entry_at: dt.datetime
    exit_at: dt.datetime
    entry: float
    exit: float
    gross_return: float
    exit_state: RecommendationState
    uses_close_fallback: bool
    net_return_5bp: float
    net_return_10bp: float
    net_return_20bp: float


class _RegimeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1]
    session_date: dt.date
    observed_at: dt.datetime
    regime: RegimeLabel
    source_version: str


@dataclass(frozen=True, slots=True)
class EvaluationSource:
    sessions: tuple[EvaluatedSession, ...]
    context: EvaluationContext


@dataclass(frozen=True, slots=True)
class AdaptiveSourceError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return self.detail


def load_evaluation_source(current_session: Path) -> EvaluationSource:
    current = _current_record(current_session)
    ledger_path = current_session.parent / "daily_research_ledger.jsonl"
    try:
        ledger = read_daily_ledger(ledger_path)
    except (OSError, ValidationError) as error:
        raise AdaptiveSourceError(f"일일 연구 원장을 읽을 수 없습니다: {ledger_path}") from error
    if not any(row.record_id == current.record_id for row in ledger):
        raise AdaptiveSourceError(f"현재 세션 record가 상위 원장에 없습니다: {current.record_id}")
    matching = tuple(
        row
        for row in ledger
        if row.session_date <= current.session_date
        and row.strategy_version == current.strategy_version
        and row.evaluator_version == current.evaluator_version
        and row.feed_entitlement == current.feed_entitlement
    )
    latest_by_date: dict[dt.date, DailyResearchRecord] = {}
    for row in sorted(matching, key=lambda item: item.recorded_at):
        latest_by_date[row.session_date] = row
    sessions = tuple(
        _load_session(current_session.parent, row)
        for row in latest_by_date.values()
        if row.session_quality.forward_day_eligible
    )
    external = tuple(
        blocker
        for blocker in current.promotion.blockers
        if not blocker.startswith(("minimum_forward_days:", "minimum_completed_trades:"))
    )
    return EvaluationSource(
        tuple(sorted(sessions, key=lambda row: row.session_date)),
        EvaluationContext(
            current.session_date,
            current.strategy_version,
            current.evaluator_version,
            external,
        ),
    )


def _current_record(session: Path) -> DailyResearchRecord:
    records = session / "daily_research_records"
    try:
        parsed = tuple(
            DailyResearchRecord.model_validate_json(path.read_text(encoding="utf-8")) for path in records.glob("*.json")
        )
    except (OSError, ValidationError) as error:
        raise AdaptiveSourceError(f"현재 세션 연구 기록을 읽을 수 없습니다: {records}") from error
    if not parsed:
        raise AdaptiveSourceError(f"현재 세션 연구 기록이 없습니다: {records}")
    return max(parsed, key=lambda row: row.recorded_at)


def _load_session(root: Path, record: DailyResearchRecord) -> EvaluatedSession:
    pattern = f"*/daily_research_records/{record.session_date}_{record.record_id[:12]}.json"
    matches = tuple(root.glob(pattern))
    if len(matches) != 1:
        raise AdaptiveSourceError(
            f"연구 기록의 세션 폴더를 하나로 결정할 수 없습니다: {record.record_id} ({len(matches)})"
        )
    session = matches[0].parents[1]
    trades = _load_trades(session, record)
    regime = _load_regime(session, record)
    database = _verified_required_artifact(session, record, DATABASE_ARTIFACT)
    risk = _verified_required_artifact(session, record, RISK_ARTIFACT)
    gap = _verified_optional_artifact(session, record, GAP_ARTIFACT)
    try:
        features = load_trade_feature_assignments(TradeFeatureSource(database, risk, gap), trades)
    except TradeCohortSourceError as error:
        raise AdaptiveSourceError(str(error)) from error
    return EvaluatedSession(record.session_date, trades, regime, features)


def _load_trades(session: Path, record: DailyResearchRecord) -> tuple[PaperTrade, ...]:
    path = _verified_required_artifact(session, record, TRADE_ARTIFACT)
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = tuple(_TradeRow.model_validate(row) for row in csv.DictReader(handle))
    except (OSError, csv.Error, ValidationError) as error:
        raise AdaptiveSourceError(f"거래 원장을 해석할 수 없습니다: {path}") from error
    return tuple(
        PaperTrade(
            row.recommendation_id,
            row.symbol,
            row.strategy,
            row.entry_at,
            row.exit_at,
            row.entry,
            row.exit,
            row.gross_return,
            row.exit_state,
            row.uses_close_fallback,
        )
        for row in rows
    )


def _load_regime(session: Path, record: DailyResearchRecord) -> str | None:
    path = _verified_optional_artifact(session, record, REGIME_ARTIFACT)
    if path is None:
        return None
    try:
        snapshot = _RegimeSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise AdaptiveSourceError(f"시장 국면 snapshot을 해석할 수 없습니다: {path}") from error
    bounds = regular_session_bounds(record.session_date)
    if bounds is None or snapshot.session_date != record.session_date:
        raise AdaptiveSourceError(f"시장 국면 snapshot 거래일이 일치하지 않습니다: {path}")
    if snapshot.observed_at.tzinfo is None:
        raise AdaptiveSourceError(f"시장 국면 snapshot 시각에 timezone이 없습니다: {path}")
    if snapshot.observed_at.astimezone(bounds[0].tzinfo) >= bounds[0]:
        raise AdaptiveSourceError(f"시장 국면 snapshot이 정규장 개장 뒤 관측됐습니다: {path}")
    return snapshot.regime


def _verified_required_artifact(
    session: Path,
    record: DailyResearchRecord,
    relative: str,
) -> Path:
    artifact = next((row for row in record.artifact_checksums if row.path == relative), None)
    if artifact is None:
        raise AdaptiveSourceError(f"필수 checksum 계보가 없습니다: {relative}")
    return _verify_artifact_path(session, artifact.path, (artifact.sha256, artifact.size_bytes))


def _verified_optional_artifact(
    session: Path,
    record: DailyResearchRecord,
    relative: str,
) -> Path | None:
    artifact = next((row for row in record.artifact_checksums if row.path == relative), None)
    if artifact is None:
        return None
    return _verify_artifact_path(session, artifact.path, (artifact.sha256, artifact.size_bytes))


def _verify_artifact_path(
    session: Path,
    relative: str,
    expected: tuple[str, int],
) -> Path:
    expected_digest, expected_size = expected
    path = session / relative
    if not path.is_file():
        raise AdaptiveSourceError(f"checksum 대상 파일이 없습니다: {path}")
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != expected_digest or path.stat().st_size != expected_size:
        raise AdaptiveSourceError(f"artifact checksum 불일치: {path}")
    return path
