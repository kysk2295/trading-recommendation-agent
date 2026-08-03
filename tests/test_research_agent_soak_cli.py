from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


def test_cli_prepare_checkpoint_status_remains_collecting_without_actual_reboot(tmp_path: Path) -> None:
    # Given: a private destination for a new actual soak.
    root = tmp_path / "private"
    database = root / "soak.sqlite3"

    # When: separate CLI processes prepare, checkpoint a restart, and query status.
    prepared = _run("prepare", database)
    restarted = _run("checkpoint", database, ("--kind", "process_restart"))
    status_result = _run("status", database)

    # Then: durable evidence exists but truthfully remains collecting without a reboot/outage/24h.
    assert prepared.returncode == restarted.returncode == status_result.returncode == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["status"] == "collecting"
    assert status_payload["actual_restart_observed"] is True
    assert status_payload["actual_reboot_observed"] is False
    assert status_payload["actual_provider_outage_observed"] is False
    assert status_payload["effects"] == {
        "broker_mutations": 0,
        "heavy_processes": 0,
        "model_calls": 0,
        "provider_requests": 0,
    }
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_cli_rejects_symlinked_parent_without_stdout_or_traceback(tmp_path: Path) -> None:
    # Given: a destination traversing a symlinked parent.
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(private, target_is_directory=True)

    # When: preparation crosses that untrusted boundary.
    result = _run("prepare", linked / "soak.sqlite3")

    # Then: the typed CLI boundary fails closed without partial JSON or traceback leakage.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "research-agent soak evidence is invalid\n"
    assert not (private / "soak.sqlite3").exists()


def test_cli_rejects_malformed_database_without_partial_output(tmp_path: Path) -> None:
    # Given: a private regular file that is not the exact soak SQLite schema.
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    database = private / "soak.sqlite3"
    database.write_bytes(b"not sqlite")
    database.chmod(0o600)

    # When: status validates the database boundary.
    result = _run("status", database)

    # Then: failure is typed and emits no partial JSON.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "research-agent soak evidence is invalid\n"


def test_cli_has_no_timestamp_injection_option(tmp_path: Path) -> None:
    # Given: a prepare command containing a caller-supplied timestamp.
    database = tmp_path / "private" / "soak.sqlite3"

    # When: the unsupported timestamp option reaches the CLI boundary.
    result = _run("prepare", database, ("--recorded-at", "2026-01-01T00:00:00Z"))

    # Then: argument parsing rejects it before any evidence is written.
    assert result.returncode == 2
    assert result.stdout == ""
    assert not database.exists()


def _run(command: str, database: Path, extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd())
    return subprocess.run(
        [sys.executable, "run_research_agent_soak.py", command, "--database", str(database), *extra],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
