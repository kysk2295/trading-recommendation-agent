from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_SCRIPT = _ROOT / "run_alpaca_sip_trade_stream_supervisor_fixture.py"


def test_supervisor_fixture_cli_recovers_then_replays_without_new_connection(tmp_path: Path) -> None:
    # Given
    state_dir = tmp_path / "state"

    # When
    first = _run("--state-dir", str(state_dir))
    replay = _run("--state-dir", str(state_dir))

    # Then
    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    first_summary = json.loads(first.stdout)
    replay_summary = json.loads(replay.stdout)
    assert first_summary == {
        "attempt_count": 1,
        "continuity_attested": False,
        "network_request_count": 0,
        "operation_count": 2,
        "sleep_seconds": [1.0],
        "status": "ready",
        "terminal_session_count": 1,
        "total_connection_count": 2,
    }
    assert replay_summary["operation_count"] == 0
    assert replay_summary["sleep_seconds"] == []
    assert replay_summary["total_connection_count"] == 2


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(_SCRIPT), *arguments),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
