from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_daily_research_record_cli import (
    RECORD_ADAPTER,
    _write_complete_session,
)


def test_daily_record_rejects_missing_candidate_input_cycle_coverage(
    tmp_path: Path,
) -> None:
    session = tmp_path / "live_sessions" / "20260714"
    _write_complete_session(session)
    (session / "candidate_input_cycles.csv").unlink()
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
    line = (session.parent / "daily_research_ledger.jsonl").read_text().strip()
    record = RECORD_ADAPTER.validate_json(line)
    assert record["session_quality"]["forward_day_eligible"] is False
    assert "candidate_input_cycle_mismatch:0/1" in record["incidents"]
    assert "data_quality_incomplete" in record["promotion"]["blockers"]
