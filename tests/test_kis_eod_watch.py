from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.kis_eod_watch import EodWaitConfig, eod_catchup_command, wait_for_eod_ready
from trading_agent.strategy_factory import StrategyMode


def test_eod_cli_exposes_the_required_output_option() -> None:
    project = Path(__file__).parents[1]
    completed = subprocess.run(
        (sys.executable, str(project / "run_kis_eod_catchup.py"), "--help"),
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--output-dir" in completed.stdout


def test_eod_command_uses_the_same_strategy_and_bounded_history(tmp_path: Path) -> None:
    command = eod_catchup_command(tmp_path, tmp_path / "session", StrategyMode.HOD_BREAKOUT, 1)

    assert "run_kis_eod_catchup.py" in command[0]
    assert command[-4:] == ("--strategy", "hod_breakout", "--max-pages", "1")


def test_watch_waits_only_near_close_until_the_last_bar_can_settle() -> None:
    new_york = ZoneInfo("America/New_York")
    times = iter(
        dt.datetime(2026, 7, 10, hour, minute, second, tzinfo=new_york)
        for hour, minute, second in (
            (15, 59, 30),
            (16, 0, 0),
            (16, 0, 30),
            (16, 1, 0),
            (16, 1, 5),
        )
    )
    waits: list[float] = []

    ready_at = wait_for_eod_ready(
        lambda: next(times),
        waits.append,
        dt.date(2026, 7, 10),
        EodWaitConfig(
            max_wait=dt.timedelta(minutes=2),
            poll_seconds=30.0,
            settlement_delay=dt.timedelta(seconds=65),
        ),
    )

    assert ready_at == dt.datetime(2026, 7, 10, 16, 1, 5, tzinfo=new_york)
    assert waits == [30.0, 30.0, 30.0, 5.0]
