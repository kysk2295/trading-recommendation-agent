from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

from tests.daily_research_fixtures import write_complete_session
from trading_agent.daily_research_ledger import build_daily_record, write_daily_record
from trading_agent.execution_store import ExecutionStore
from trading_agent.intraday_broker_shadow_publication import (
    BrokerShadowPublicationRequest,
    load_broker_shadow_evidence_artifact,
    publish_broker_shadow_evidence,
)
from trading_agent.strategy_factory import StrategyMode

REVIEWED_AT = dt.datetime(2026, 7, 24, 6, 0, tzinfo=dt.UTC)


def test_publication_binds_exact_research_and_execution_snapshots(tmp_path: Path) -> None:
    # Given: one eligible shadow session and an initialized empty Paper ledger.
    session, ledger = _sources(tmp_path)
    output = tmp_path / "evidence"
    request = BrokerShadowPublicationRequest(session, ledger, output, REVIEWED_AT)

    # When: the query-only diagnostic is published twice from identical sources.
    first, first_created = publish_broker_shadow_evidence(request)
    second, second_created = publish_broker_shadow_evidence(request)

    # Then: one private content-addressed artifact binds both exact sources.
    path = output / f"intraday_broker_shadow_evidence_{first.artifact_id}.json"
    loaded = load_broker_shadow_evidence_artifact(path)
    assert first == second == loaded
    assert first_created is True
    assert second_created is False
    assert first.payload.status.value == "collecting"
    assert first.payload.paired_trade_count == 0
    assert first.payload.execution_snapshot_sha256 == (
        ExecutionStore(ledger).ledger_snapshot_identity().sha256
    )
    assert len(first.payload.shadow_source_sha256) == 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_cli_publishes_collecting_report_without_mutation_authority(tmp_path: Path) -> None:
    # Given: verified research lineage and a query-only Paper ledger source.
    session, ledger = _sources(tmp_path)
    output = tmp_path / "cli-output"

    # When: an operator runs the public CLI surface.
    completed = _run_cli(session, ledger, output)

    # Then: collecting evidence and an explicit no-authority report are emitted.
    assert completed.returncode == 0, completed.stderr
    report = output / "intraday_broker_shadow_evidence_ko.md"
    text = report.read_text(encoding="utf-8")
    assert "- result: collecting" in text
    assert "- paired trades: 0" in text
    assert "- automatic state change: false" in text
    assert "- order authority change: false" in text
    assert "- allocation change: false" in text
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    artifacts = tuple(output.glob("intraday_broker_shadow_evidence_*.json"))
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["artifact_id"] in artifacts[0].name


def test_cli_fails_closed_for_uninitialized_execution_ledger(tmp_path: Path) -> None:
    # Given: valid research but no initialized execution ledger.
    session, _ = _sources(tmp_path)
    missing = tmp_path / "missing.sqlite3"
    output = tmp_path / "blocked-output"

    # When: the public CLI is given the missing ledger.
    completed = _run_cli(session, missing, output)

    # Then: it blocks without emitting a promotion artifact.
    assert completed.returncode == 1
    assert not tuple(output.glob("intraday_broker_shadow_evidence_*.json"))
    report = output / "intraday_broker_shadow_evidence_ko.md"
    assert "- result: blocked" in report.read_text(encoding="utf-8")


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    session = tmp_path / "live_sessions" / "20260714_forward_actual"
    write_complete_session(session)
    record = build_daily_record(
        session,
        dt.date(2026, 7, 14),
        StrategyMode.ORB,
        "test-code",
        dt.datetime(2026, 7, 14, 21, 0, tzinfo=dt.UTC),
    )
    assert record.session_quality.forward_day_eligible
    assert write_daily_record(session, record)
    ledger = tmp_path / "paper_execution.sqlite3"
    with ExecutionStore(ledger).writer():
        pass
    return session, ledger


def _run_cli(
    session: Path,
    ledger: Path,
    output: Path,
) -> subprocess.CompletedProcess[str]:
    project = Path(__file__).parents[1]
    return subprocess.run(
        (
            sys.executable,
            str(project / "run_intraday_broker_shadow_evidence.py"),
            "--current-session",
            str(session),
            "--execution-ledger",
            str(ledger),
            "--reviewed-at",
            REVIEWED_AT.isoformat(),
            "--output-dir",
            str(output),
        ),
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
