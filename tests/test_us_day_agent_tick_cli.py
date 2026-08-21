from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _fake_executable(path: Path, marker: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text('called')\n"
        "args = dict(zip(sys.argv[1::2], sys.argv[2::2], strict=True))\n"
        "if args['--operation'] == 'regular':\n"
        " print(json.dumps({'status':'accepted','phase':'regular','tick_id':args['--tick-id'],"
        "'recommendation_id':'rec-local','paper_eligible':True}))\n"
        "elif args['--operation'] == 'paper': print(json.dumps({'paper_status':'completed'}))\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def test_help_has_no_broker_endpoint_option() -> None:
    # Given / When: the CLI help is requested without credentials or providers.
    completed = subprocess.run(
        [sys.executable, "run_us_day_agent_tick.py", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: help succeeds and exposes no broker URL authority.
    assert completed.returncode == 0
    assert "--broker" not in completed.stdout
    assert "--base-url" not in completed.stdout


def test_stale_situation_blocks_with_compact_json_before_runtime(tmp_path: Path) -> None:
    # Given: a canonical but stale local situation fixture.
    fixture = ROOT / "tests/fixtures/day-agent/stale-situation.json"
    marker = tmp_path / "called"
    executable = _fake_executable(tmp_path / "fake-agent", marker)

    # When: the scheduler CLI validates it.
    completed = subprocess.run(
        [
            sys.executable,
            "run_us_day_agent_tick.py",
            "--situation",
            str(fixture),
            "--outputs",
            str(tmp_path),
            "--now",
            "2026-08-21T15:00:00Z",
            "--agent-executable",
            str(executable),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: output is stable, redacted, and nonzero.
    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "phase": "regular",
        "reason": "situation_stale",
        "status": "blocked",
    }
    assert completed.stderr == ""
    assert not marker.exists()


def test_fresh_local_situation_runs_bound_executable_and_emits_ids_only(tmp_path: Path) -> None:
    # Given: a current regular-session situation and a local fake vertical executable.
    situation = tmp_path / "situation.json"
    situation.write_text(
        json.dumps({"evaluated_at": "2026-08-21T14:59:00Z", "session_id": "XNYS-2026-08-21"}),
        encoding="utf-8",
    )
    marker = tmp_path / "called"
    executable = _fake_executable(tmp_path / "fake-agent", marker)

    # When: the scheduler runs the local happy path.
    completed = subprocess.run(
        [
            sys.executable,
            "run_us_day_agent_tick.py",
            "--situation",
            str(situation),
            "--outputs",
            str(tmp_path / "outputs"),
            "--now",
            "2026-08-21T15:00:00Z",
            "--agent-executable",
            str(executable),
            "--reasoning-model",
            "local-test-model",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: recovery and regular execution complete with compact identifiers only.
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "paper_status": "completed",
        "phase": "regular",
        "recommendation_id": "rec-local",
        "status": "accepted",
    }
    assert marker.read_text(encoding="utf-8") == "called"
    assert completed.stderr == ""
