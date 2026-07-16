from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import cast

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    EVALUATOR_VERSION,
    FEED_ENTITLEMENT,
    SHADOW_PORTFOLIO_POLICY,
    promotion_blockers,
    strategy_contract,
    strategy_version_identity,
)
from trading_agent.daily_research_models import (
    DailyResearchRecord,
    PromotionAssessment,
)
from trading_agent.daily_research_sources import (
    data_version,
    load_20bp_metrics,
    load_artifacts,
    load_session_quality,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_contract_models import (
    require_scope_registered_before_session,
    single_lane_experiment_scope,
)
from trading_agent.lane_defaults import LANE_CONTRACT_REGISTERED_AT
from trading_agent.lane_policy_models import LaneId
from trading_agent.strategy_factory import StrategyMode


def build_daily_record(
    session: Path,
    session_date: dt.date,
    strategy: StrategyMode,
    code_version: str,
    recorded_at: dt.datetime,
) -> DailyResearchRecord:
    contract = strategy_contract(strategy)
    strategy_version = strategy_version_identity(strategy, code_version)
    require_scope_registered_before_session(contract.experiment_scope, session_date)
    scope_key = experiment_scope_key(contract.experiment_scope)
    artifacts = load_artifacts(session)
    current_data_version = data_version(artifacts)
    metrics = load_20bp_metrics(session / "paper_metrics/paper_metrics.csv")
    quality, incidents = load_session_quality(session, metrics.trade_count)
    prior = read_daily_ledger(session.parent / "daily_research_ledger.jsonl")
    prior_for_strategy = {
        row.session_date: row
        for row in prior
        if row.strategy_version == strategy_version
        and row.experiment_scope_key == scope_key
        and row.evaluator_version == EVALUATOR_VERSION
        and row.feed_entitlement == FEED_ENTITLEMENT
        and row.session_date < session_date
    }
    cumulative_days = sum(row.session_quality.forward_day_eligible for row in prior_for_strategy.values()) + int(
        quality.forward_day_eligible
    )
    cumulative_trades = (
        sum(row.session_quality.eligible_completed_trades for row in prior_for_strategy.values())
        + quality.eligible_completed_trades
    )
    blockers = promotion_blockers(quality, cumulative_days, cumulative_trades)
    record_id = hashlib.sha256(
        (
            f"{session_date.isoformat()}|{strategy_version}|"
            f"{scope_key}|"
            f"{code_version}|{EVALUATOR_VERSION}|{current_data_version}|"
            f"{cumulative_days}|{cumulative_trades}|{'|'.join(blockers)}"
        ).encode()
    ).hexdigest()
    return DailyResearchRecord(
        schema_version=2,
        record_id=record_id,
        recorded_at=recorded_at,
        session_date=session_date,
        hypothesis_id=contract.hypothesis_id,
        hypothesis=contract.hypothesis,
        falsification_rule=contract.falsification_rule,
        strategy=strategy.value,
        strategy_version=strategy_version,
        strategy_stage="experimental_shadow",
        experiment_scope=contract.experiment_scope,
        experiment_scope_key=scope_key,
        code_version=code_version,
        evaluator_version=EVALUATOR_VERSION,
        data_version=current_data_version,
        feed_entitlement=FEED_ENTITLEMENT,
        parameter_set=contract.parameter_set,
        cost_model=CURRENT_COST_MODEL,
        portfolio_policy=SHADOW_PORTFOLIO_POLICY,
        session_quality=quality,
        metrics_20bp=metrics,
        incidents=incidents,
        promotion=PromotionAssessment(
            allowed=not blockers,
            cumulative_forward_days=cumulative_days,
            cumulative_completed_trades=cumulative_trades,
            blockers=blockers,
        ),
        artifact_checksums=artifacts,
    )


def write_daily_record(session: Path, record: DailyResearchRecord) -> bool:
    encoded = record.model_dump_json()
    records = session / "daily_research_records"
    records.mkdir(parents=True, exist_ok=True)
    record_path = records / f"{record.session_date}_{record.record_id[:12]}.json"
    created = not record_path.is_file()
    if created:
        temporary = record_path.with_suffix(".tmp")
        _ = temporary.write_text(encoded + "\n", encoding="utf-8")
        temporary.replace(record_path)
    ledger = session.parent / "daily_research_ledger.jsonl"
    existing_ids = {row.record_id for row in read_daily_ledger(ledger)}
    if record.record_id not in existing_ids:
        with ledger.open("a", encoding="utf-8") as handle:
            _ = handle.write(encoded + "\n")
    _write_summary(session / "daily_research_summary_ko.md", record)
    return created


def read_daily_ledger(path: Path) -> tuple[DailyResearchRecord, ...]:
    if not path.is_file():
        return ()
    return tuple(parse_daily_record(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def parse_daily_record(encoded: str) -> DailyResearchRecord:
    try:
        decoded_object: object = json.loads(encoded)
    except json.JSONDecodeError:
        return DailyResearchRecord.model_validate_json(encoded)
    if not isinstance(decoded_object, dict):
        return DailyResearchRecord.model_validate(decoded_object)
    decoded = cast(dict[str, object], decoded_object)
    if decoded.get("schema_version") == 1:
        hypothesis_id = decoded.get("hypothesis_id")
        if not isinstance(hypothesis_id, str):
            return DailyResearchRecord.model_validate(decoded)
        scope = single_lane_experiment_scope(
            LaneId.INTRADAY_MOMENTUM,
            hypothesis_id,
            LANE_CONTRACT_REGISTERED_AT,
        )
        decoded = {
            **decoded,
            "schema_version": 2,
            "experiment_scope": scope.model_dump(mode="json"),
            "experiment_scope_key": experiment_scope_key(scope),
        }
    return DailyResearchRecord.model_validate(decoded)


def _write_summary(path: Path, record: DailyResearchRecord) -> None:
    quality = record.session_quality
    metrics = record.metrics_20bp
    lines = [
        "# 일일 Paper 연구 원장",
        "",
        "> 확정 수익이 아닌 shadow Paper 전진검증 기록입니다.",
        "",
        f"- 거래일: {record.session_date}",
        f"- 가설: {record.hypothesis_id} / {record.hypothesis}",
        f"- 전략 버전: {record.strategy_version}",
        f"- 연구 lane: {record.experiment_scope.primary_lane.value}",
        f"- experiment scope: {record.experiment_scope_key}",
        f"- 코드 버전: {record.code_version}",
        f"- 데이터 버전: {record.data_version}",
        f"- 평가기 버전: {record.evaluator_version}",
        f"- 품질 적격 거래일: {quality.forward_day_eligible}",
        (
            "- KIS 읽기 재시도/복구/반복실패: "
            f"{quality.read_retries}/{quality.read_retry_recoveries}/{quality.read_retry_failures}"
        ),
        (
            "- 후보 입력 cycle/선정/snapshot: "
            f"{quality.candidate_input_cycles}/{quality.candidate_input_selections}/"
            f"{quality.candidate_inputs}"
        ),
        f"- 완료 shadow 거래: {quality.completed_trades}건",
        f"- 편도 20bp PF: {_number(metrics.profit_factor)}",
        f"- 편도 20bp 평균: {_percent(metrics.average_return)}",
        "- 승격 금지: " + ", ".join(record.promotion.blockers),
        "",
        "## 운영 incident",
        "",
    ]
    lines.extend(f"- {incident}" for incident in record.incidents)
    if not record.incidents:
        lines.append("- 없음")
    _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _number(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"
