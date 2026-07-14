from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import run_kis_paper_watch
from trading_agent.store import PaperStore


def test_watch_runs_metrics_only_after_the_regular_session_close(
    tmp_path: Path,
) -> None:
    # Given: a session database exists and a fake subprocess boundary records calls.
    _ = PaperStore(tmp_path / "paper_recommendations.sqlite3")
    calls: list[tuple[tuple[str, ...], Path]] = []

    def run(command: tuple[str, ...], audit_path: Path) -> int:
        calls.append((command, audit_path))
        return 0

    new_york = ZoneInfo("America/New_York")
    during_session = dt.datetime(2026, 7, 10, 15, 59, tzinfo=new_york)
    after_close = dt.datetime(2026, 7, 10, 16, 0, tzinfo=new_york)

    # When: the same session requests metrics before and after the close.
    early_result = run_kis_paper_watch.run_session_metrics(
        tmp_path,
        during_session,
        run,
    )
    final_result = run_kis_paper_watch.run_session_metrics(
        tmp_path,
        after_close,
        run,
    )

    # Then: only the closed session runs the existing metrics CLI and audits it.
    assert early_result is None
    assert final_result == 0
    assert len(calls) == 1
    command, audit_path = calls[0]
    assert command[0].endswith("run_paper_metrics.py")
    assert command[1] == str(tmp_path / "paper_recommendations.sqlite3")
    assert command[-1] == str(tmp_path / "paper_metrics")
    assert audit_path == tmp_path / "post_session_metrics_cycles.csv"
