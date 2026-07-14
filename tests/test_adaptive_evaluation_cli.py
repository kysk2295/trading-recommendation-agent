from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.test_daily_research_record_cli import _write_complete_session


def test_adaptive_cli_writes_daily_card_from_immutable_trade_lineage(tmp_path: Path) -> None:
    # Given: one eligible session in a suffixed production-style directory.
    session = tmp_path / "live_sessions" / "20260714_forward_actual"
    _write_complete_session(session)
    _record(session)

    # When: the adaptive evaluator runs through its CLI.
    completed = _evaluate(session)

    # Then: it writes a machine record and Korean card without changing strategy state.
    assert completed.returncode == 0, completed.stderr
    output = session / "adaptive_evaluation"
    payload = json.loads((output / "adaptive_evaluation.json").read_text(encoding="utf-8"))
    assert payload["action"] == "collecting"
    assert payload["automatic_state_change_allowed"] is False
    assert payload["windows"][0]["observed_sessions"] == 1
    report = (output / "adaptive_evaluation_ko.md").read_text(encoding="utf-8")
    assert "60일은 수익 확정이 아니라 최종 검토 문턱" in report
    assert "자동 상태 변경: 금지" in report


def test_adaptive_cli_rejects_trade_file_changed_after_daily_record(tmp_path: Path) -> None:
    # Given: a recorded eligible session whose trade CSV is changed afterward.
    session = tmp_path / "live_sessions" / "20260714_forward_actual"
    _write_complete_session(session)
    _record(session)
    trades = session / "paper_metrics" / "paper_trades.csv"
    with trades.open("a", encoding="utf-8") as handle:
        _ = handle.write("\n")

    # When: the adaptive evaluator checks the immutable lineage.
    completed = _evaluate(session)

    # Then: it fails closed and emits no evaluation artifact.
    assert completed.returncode == 2
    assert "checksum" in completed.stderr
    assert not (session / "adaptive_evaluation" / "adaptive_evaluation.json").exists()


def test_adaptive_cli_segments_only_preopen_regime_snapshot(tmp_path: Path) -> None:
    # Given: a point-in-time market regime snapshot observed before the regular open.
    session = tmp_path / "live_sessions" / "20260714_forward_actual"
    _write_complete_session(session)
    (session / "research_regime_snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_date": "2026-07-14",
                "observed_at": "2026-07-14T09:00:00-04:00",
                "regime": "risk_on_high_vol",
                "source_version": "fixture-v1",
            }
        ),
        encoding="utf-8",
    )
    _record(session)

    # When: the adaptive evaluator loads the session.
    completed = _evaluate(session)

    # Then: the causal regime label is included in segmented evidence.
    assert completed.returncode == 0, completed.stderr
    output = session / "adaptive_evaluation" / "adaptive_evaluation.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["regime_coverage"] == 1.0
    assert payload["regimes"][0]["regime"] == "risk_on_high_vol"


def test_adaptive_cli_rejects_regime_snapshot_observed_after_open(tmp_path: Path) -> None:
    # Given: a regime label created after trading could already have started.
    session = tmp_path / "live_sessions" / "20260714_forward_actual"
    _write_complete_session(session)
    (session / "research_regime_snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_date": "2026-07-14",
                "observed_at": "2026-07-14T09:30:00-04:00",
                "regime": "risk_on_high_vol",
                "source_version": "fixture-v1",
            }
        ),
        encoding="utf-8",
    )
    _record(session)

    # When: the adaptive evaluator validates regime causality.
    completed = _evaluate(session)

    # Then: the post-open label is rejected rather than used retrospectively.
    assert completed.returncode == 2
    assert "정규장 개장 뒤" in completed.stderr


def test_adaptive_cli_rejects_current_record_missing_from_parent_ledger(tmp_path: Path) -> None:
    # Given: a session record exists but its append-only parent ledger is missing.
    session = tmp_path / "live_sessions" / "20260714_forward_actual"
    _write_complete_session(session)
    _record(session)
    (session.parent / "daily_research_ledger.jsonl").unlink()

    # When: the adaptive evaluator resolves cumulative evidence.
    completed = _evaluate(session)

    # Then: it rejects the broken lineage instead of reporting zero prior days.
    assert completed.returncode == 2
    assert "상위 원장" in completed.stderr


def _record(session: Path) -> None:
    project = Path(__file__).parents[1]
    completed = subprocess.run(
        (
            sys.executable,
            str(project / "run_daily_research_record.py"),
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
    assert completed.returncode == 0, completed.stderr


def _evaluate(session: Path) -> subprocess.CompletedProcess[str]:
    project = Path(__file__).parents[1]
    return subprocess.run(
        (sys.executable, str(project / "run_adaptive_strategy_evaluation.py"), str(session)),
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
