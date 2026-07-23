from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

from tests.daily_research_fixtures import write_complete_session

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_forward_session_progress.py"
REPORT_NAME = "forward_session_progress_ko.md"


def test_cli_reports_clean_in_progress_strict_invariants(tmp_path: Path) -> None:
    # Given
    session = tmp_path / "live_sessions/20260714"
    write_complete_session(session)
    output = tmp_path / "progress"

    # When
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            str(session),
            "--minimum-watch-cycles",
            "1",
            "--output-dir",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    report = output / REPORT_NAME
    content = report.read_text(encoding="utf-8")
    assert "- result: progress_clean" in content
    assert "- watch cycles: 1" in content
    assert "- final eligibility: pending_post_session" in content
    assert "- external provider/account/order mutation: 0" in content
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
