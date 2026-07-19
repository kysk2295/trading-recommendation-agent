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
    first = _run("--state-dir", str(state_dir), "--run-id", "a" * 64)
    replay = _run("--state-dir", str(state_dir), "--run-id", "b" * 64)

    # Then
    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    first_summary = json.loads(first.stdout)
    replay_summary = json.loads(replay.stdout)
    assert first_summary == {
        "attempt_count": 1,
        "audit_event_count": 5,
        "audit_terminal_kind": "ready",
        "continuity_attested": False,
        "network_request_count": 0,
        "operation_count": 2,
        "sleep_seconds": [1.0],
        "status": "ready",
        "terminal_session_count": 1,
        "total_connection_count": 2,
    }
    assert replay_summary["operation_count"] == 0
    assert replay_summary["audit_event_count"] == 2
    assert replay_summary["audit_terminal_kind"] == "ready"
    assert replay_summary["sleep_seconds"] == []
    assert replay_summary["total_connection_count"] == 2


def test_supervisor_fixture_cli_shutdown_records_stopped_without_connection(tmp_path: Path) -> None:
    # Given / When
    result = _run(
        "--state-dir",
        str(tmp_path / "state"),
        "--run-id",
        "c" * 64,
        "--shutdown-before-operation",
    )

    # Then
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "stopped"
    assert summary["operation_count"] == 0
    assert summary["total_connection_count"] == 0
    assert summary["audit_event_count"] == 2
    assert summary["audit_terminal_kind"] == "stopped"
    assert summary["network_request_count"] == 0


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(_SCRIPT), *arguments),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
