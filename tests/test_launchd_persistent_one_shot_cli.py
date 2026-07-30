from __future__ import annotations

import json
import plistlib
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_launchd_one_shot.py"


def test_persistent_runner_survives_stale_claim_and_executes_once(tmp_path: Path) -> None:
    # Given: a clean current-main repository and a reboot-safe payload.
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Scheduler Test")
    _git(repository, "config", "user.email", "scheduler@example.invalid")
    (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "fixture")
    _git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    source_commit = _git(repository, "rev-parse", "HEAD").stdout.strip()
    payload = tmp_path / "payload.zsh"
    counter = tmp_path / "payload-runs.txt"
    payload.write_text("#!/bin/zsh\nprint -r -- run >> \"$1\"\n", encoding="utf-8")
    payload.chmod(0o700)
    output = tmp_path / "scheduled"
    wrapper = output / "runner.zsh"
    receipt = output / "receipt.json"
    persistent_plist = tmp_path / "LaunchAgents/ai.trading-agent.pytest-persistent.plist"

    # When: the persistent job is prepared, then starts after a reboot-stale claim.
    prepared = subprocess.run(
        (
            "uv",
            "run",
            "--script",
            str(SCRIPT),
            "--label",
            "ai.trading-agent.pytest-persistent",
            "--run-at",
            "1970-01-01T00:00:00+00:00",
            "--expires-at",
            "2100-01-01T00:00:00+00:00",
            "--wrapper",
            str(wrapper),
            "--stdout-log",
            str(output / "stdout.log"),
            "--stderr-log",
            str(output / "stderr.log"),
            "--receipt",
            str(receipt),
            "--persistent-plist",
            str(persistent_plist),
            "--authority-repository",
            str(repository),
            "--recovery-safe",
            "--prepare-only",
            "--",
            str(payload),
            str(counter),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert prepared.returncode == 0
    with persistent_plist.open("rb") as handle:
        launch_agent = plistlib.load(handle)
    (Path(f"{receipt}.claim")).mkdir()
    first = subprocess.run((str(wrapper),), check=False, capture_output=True, text=True)
    second = subprocess.run((str(wrapper),), check=False, capture_output=True, text=True)

    # Then: the stale claim is recovered, current-main is bound, and replay is suppressed.
    assert first.returncode == 0
    assert second.returncode == 0
    assert counter.read_text(encoding="utf-8").splitlines() == ["run"]
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "completed_at_epoch": int(
            json.loads(receipt.read_text(encoding="utf-8"))["completed_at_epoch"]
        ),
        "exit_code": 0,
        "label": "ai.trading-agent.pytest-persistent",
        "result": "completed",
        "schema_version": 1,
        "source_commit_sha": source_commit,
    }
    assert launch_agent["Label"] == "ai.trading-agent.pytest-persistent"
    assert launch_agent["ProgramArguments"] == ["/bin/zsh", str(wrapper)]
    assert launch_agent["RunAtLoad"] is True
    assert stat.S_IMODE(wrapper.stat().st_mode) == 0o700
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert not persistent_plist.exists()


def test_expired_persistent_runner_does_not_execute_payload(tmp_path: Path) -> None:
    # Given
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Scheduler Test")
    _git(repository, "config", "user.email", "scheduler@example.invalid")
    (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "fixture")
    _git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    payload = tmp_path / "payload.zsh"
    counter = tmp_path / "payload-runs.txt"
    payload.write_text("#!/bin/zsh\nprint -r -- run >> \"$1\"\n", encoding="utf-8")
    payload.chmod(0o700)
    output = tmp_path / "scheduled"
    wrapper = output / "runner.zsh"
    receipt = output / "receipt.json"

    # When
    prepared = subprocess.run(
        (
            "uv",
            "run",
            "--script",
            str(SCRIPT),
            "--label",
            "ai.trading-agent.pytest-expired",
            "--run-at",
            "1970-01-01T00:00:00+00:00",
            "--expires-at",
            "1970-01-01T00:01:00+00:00",
            "--wrapper",
            str(wrapper),
            "--stdout-log",
            str(output / "stdout.log"),
            "--stderr-log",
            str(output / "stderr.log"),
            "--receipt",
            str(receipt),
            "--persistent-plist",
            str(tmp_path / "LaunchAgents/ai.trading-agent.pytest-expired.plist"),
            "--authority-repository",
            str(repository),
            "--recovery-safe",
            "--prepare-only",
            "--",
            str(payload),
            str(counter),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert prepared.returncode == 0
    completed = subprocess.run((str(wrapper),), check=False, capture_output=True, text=True)

    # Then
    assert completed.returncode == 0
    assert not counter.exists()
    assert json.loads(receipt.read_text(encoding="utf-8"))["result"] == "expired"


def test_prepare_rejects_partial_persistent_contract(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "scheduled"

    # When
    completed = subprocess.run(
        (
            "uv",
            "run",
            "--script",
            str(SCRIPT),
            "--label",
            "ai.trading-agent.pytest-partial-persistent",
            "--run-at",
            "1970-01-01T00:00:00+00:00",
            "--wrapper",
            str(output / "runner.zsh"),
            "--stdout-log",
            str(output / "stdout.log"),
            "--stderr-log",
            str(output / "stderr.log"),
            "--receipt",
            str(output / "receipt.json"),
            "--recovery-safe",
            "--prepare-only",
            "--",
            "/usr/bin/true",
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 2
    assert completed.stderr == '{"reason": "invalid_request", "result": "blocked"}\n'
    assert not output.exists()


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
