from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_SCRIPT = _ROOT / "run_alpaca_sip_trade_stream_attempt_fixture.py"


def test_attempt_fixture_cli_help_describes_local_scenarios() -> None:
    completed = _run("--help")

    assert completed.returncode == 0
    assert "--scenario" in completed.stdout
    assert "--stream-store" in completed.stdout


def test_attempt_fixture_cli_connection_limit_persists_sanitized_evidence(
    tmp_path: Path,
) -> None:
    stream_store = tmp_path / "stream.sqlite3"

    completed = _run(
        "--scenario",
        "connection-limit",
        "--stream-store",
        str(stream_store),
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "attempt_count": 1,
        "control_count": 1,
        "failure_code": "connection_limit",
        "network_request_count": 0,
        "stage": "connected_control",
        "terminal_session_count": 0,
    }
    assert stat.S_IMODE(stream_store.stat().st_mode) == 0o600


def test_attempt_fixture_cli_handshake_failure_has_no_control(tmp_path: Path) -> None:
    completed = _run(
        "--scenario",
        "handshake-failure",
        "--stream-store",
        str(tmp_path / "stream.sqlite3"),
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["failure_code"] == "handshake_failed"
    assert summary["stage"] == "connect"
    assert summary["control_count"] == 0
    assert summary["network_request_count"] == 0


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(_SCRIPT), *arguments),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
