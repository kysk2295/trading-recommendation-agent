from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

from tests.daily_research_fixtures import write_complete_session
from tests.test_daily_research_record_cli import RECORD_ADAPTER


def test_new_evaluator_does_not_inherit_legacy_evaluator_counts(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "live_sessions"
    first = sessions / "20260714"
    second = sessions / "20260715"
    write_complete_session(first, dt.date(2026, 7, 14))
    write_complete_session(second, dt.date(2026, 7, 15))
    project = Path(__file__).parents[1]
    script = project / "run_daily_research_record.py"
    first_run = subprocess.run(
        (
            sys.executable,
            str(script),
            str(first),
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
    assert first_run.returncode == 0, first_run.stderr
    ledger = sessions / "daily_research_ledger.jsonl"
    legacy = ledger.read_text(encoding="utf-8").replace(
        "paper_metrics_day_block_bootstrap_v2",
        "paper_metrics_trade_bootstrap_v1",
    )
    _ = ledger.write_text(legacy, encoding="utf-8")

    second_run = subprocess.run(
        (
            sys.executable,
            str(script),
            str(second),
            "--session-date",
            "2026-07-15",
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

    assert second_run.returncode == 0, second_run.stderr
    records = tuple(RECORD_ADAPTER.validate_json(line) for line in ledger.read_text().splitlines())
    assert records[-1]["promotion"]["cumulative_forward_days"] == 1
