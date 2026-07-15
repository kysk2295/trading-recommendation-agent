from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

from pydantic import TypeAdapter

from tests.daily_research_fixtures import write_complete_session


class QualityJson(TypedDict):
    forward_day_eligible: bool
    completed_trades: int
    candidate_input_cycles: int
    candidate_input_selections: int
    candidate_inputs: int
    read_retries: int
    read_retry_recoveries: int
    read_retry_failures: int


class MetricsJson(TypedDict):
    side_cost_bps: int
    trade_count: int


class PromotionJson(TypedDict):
    allowed: bool
    cumulative_forward_days: int
    blockers: list[str]


class ArtifactJson(TypedDict):
    path: str


class ExperimentScopeJson(TypedDict):
    hypothesis_id: str
    primary_lane: str
    lanes: list[str]


class DailyRecordJson(TypedDict):
    schema_version: int
    session_date: str
    code_version: str
    evaluator_version: str
    strategy_stage: str
    experiment_scope: ExperimentScopeJson
    experiment_scope_key: str
    session_quality: QualityJson
    metrics_20bp: MetricsJson
    incidents: list[str]
    promotion: PromotionJson
    artifact_checksums: list[ArtifactJson]


RECORD_ADAPTER = TypeAdapter(DailyRecordJson)


def test_daily_research_cli_writes_lineage_and_blocks_early_promotion(
    tmp_path: Path,
) -> None:
    # Given: one complete ORB shadow trade and one fully covered provider cycle.
    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session)
    project = Path(__file__).parents[1]
    script = project / "run_daily_research_record.py"
    assert script.is_file(), "daily research CLI is missing"

    # When: the closed session is recorded with an explicit code version.
    completed = subprocess.run(
        (
            sys.executable,
            str(script),
            str(session),
            "--session-date",
            "2026-07-14",
            "--strategy",
            "orb",
            "--code-version",
            "test-code",
        ),
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: immutable lineage exists but the 60-day/100-trade gate blocks promotion.
    assert completed.returncode == 0, completed.stderr
    ledger = session.parent / "daily_research_ledger.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = RECORD_ADAPTER.validate_json(lines[0])
    assert record["schema_version"] == 2
    assert record["session_date"] == "2026-07-14"
    assert record["code_version"] == "test-code"
    assert record["evaluator_version"] == "paper_metrics_day_block_bootstrap_v2"
    assert record["strategy_stage"] == "experimental_shadow"
    assert record["experiment_scope"]["hypothesis_id"] == "H-MOM-ORB-001"
    assert record["experiment_scope"]["primary_lane"] == "intraday_momentum"
    assert record["experiment_scope"]["lanes"] == ["intraday_momentum"]
    assert len(record["experiment_scope_key"]) == 64
    assert record["session_quality"]["forward_day_eligible"] is True
    assert record["session_quality"]["completed_trades"] == 1
    assert record["session_quality"]["candidate_input_cycles"] == 1
    assert record["session_quality"]["candidate_input_selections"] == 1
    assert record["session_quality"]["candidate_inputs"] == 1
    assert record["session_quality"]["read_retries"] == 1
    assert record["session_quality"]["read_retry_recoveries"] == 1
    assert record["session_quality"]["read_retry_failures"] == 0
    assert "kis_read_retries:1" in record["incidents"]
    assert "kis_read_recoveries:1" in record["incidents"]
    artifact_paths = {artifact["path"] for artifact in record["artifact_checksums"]}
    assert "kis_read_retry_cycles.csv" in artifact_paths
    assert "kis_read_retry_events.csv" in artifact_paths
    assert "candidate_input_cycles.csv" in artifact_paths
    assert "paper_metrics/paper_trades.csv" in artifact_paths
    assert "kis_opening_gap_snapshots.csv" in artifact_paths
    assert record["metrics_20bp"]["side_cost_bps"] == 20
    assert record["metrics_20bp"]["trade_count"] == 1
    assert record["promotion"]["allowed"] is False
    assert "minimum_forward_days:1/60" in record["promotion"]["blockers"]
    assert "minimum_completed_trades:1/100" in record["promotion"]["blockers"]
    assert "block_bootstrap_missing" not in record["promotion"]["blockers"]
    summary = (session / "daily_research_summary_ko.md").read_text(encoding="utf-8")
    assert "승격 금지" in summary
    assert "확정 수익" in summary
    assert "연구 lane: intraday_momentum" in summary


def test_rerunning_older_session_does_not_use_future_ledger_rows(
    tmp_path: Path,
) -> None:
    # Given: two eligible sessions were recorded in chronological order.
    sessions = tmp_path / "live_sessions"
    first = sessions / "20260714"
    second = sessions / "20260715"
    write_complete_session(first, dt.date(2026, 7, 14))
    write_complete_session(second, dt.date(2026, 7, 15))
    project = Path(__file__).parents[1]
    script = project / "run_daily_research_record.py"
    for session, session_date in (
        (first, "2026-07-14"),
        (second, "2026-07-15"),
        (first, "2026-07-14"),
    ):
        completed = subprocess.run(
            (
                sys.executable,
                str(script),
                str(session),
                "--session-date",
                session_date,
                "--strategy",
                "orb",
                "--code-version",
                "test-code",
            ),
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    # Then: the older replay is idempotent and retains its original as-of total.
    lines = (sessions / "daily_research_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = tuple(RECORD_ADAPTER.validate_json(line) for line in lines)
    first_record = next(row for row in records if row["session_date"] == "2026-07-14")
    assert first_record["promotion"]["cumulative_forward_days"] == 1
