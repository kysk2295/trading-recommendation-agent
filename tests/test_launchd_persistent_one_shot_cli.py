from __future__ import annotations

import datetime as dt
import hashlib
import json
import plistlib
import stat
import subprocess
from pathlib import Path

from trading_agent.launchd_one_shot_runner import (
    OneShotRunnerSpec,
    render_persistent_runner,
)

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
    payload.write_text('#!/bin/zsh\nprint -r -- run >> "$1"\n', encoding="utf-8")
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
        "completed_at_epoch": int(json.loads(receipt.read_text(encoding="utf-8"))["completed_at_epoch"]),
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
    payload.write_text('#!/bin/zsh\nprint -r -- run >> "$1"\n', encoding="utf-8")
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


def test_provenance_bound_runner_writes_schema_v2_receipt(tmp_path: Path) -> None:
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
    source_commit = _git(repository, "rev-parse", "HEAD").stdout.strip()
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version":2}\n', encoding="utf-8")
    manifest.chmod(0o600)
    receipt = tmp_path / "receipt.json"
    plist = tmp_path / "persistent.plist"
    plist.touch(mode=0o600)
    wrapper = tmp_path / "runner.zsh"
    wrapper.write_text(
        render_persistent_runner(
            OneShotRunnerSpec(
                label="ai.trading-agent.pytest-provenance",
                run_at=dt.datetime(1970, 1, 1, tzinfo=dt.UTC),
                receipt=receipt,
                command=("/usr/bin/true",),
                expires_at=dt.datetime(2100, 1, 1, tzinfo=dt.UTC),
                persistent_plist=plist,
                authority_repository=repository,
                source_commit=source_commit,
                role="us_orb_watcher",
                request_sha256="a" * 64,
                plan_sha256="b" * 64,
                runtime_commit_sha="c" * 40,
                runtime_attestation_sha256="d" * 64,
                preparation_manifest=manifest,
            )
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o700)

    # When
    completed = subprocess.run(
        (str(wrapper),),
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert payload == {
        "completed_at_epoch": payload["completed_at_epoch"],
        "exit_code": 0,
        "label": "ai.trading-agent.pytest-provenance",
        "plan_sha256": "b" * 64,
        "preparation_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "request_sha256": "a" * 64,
        "result": "completed",
        "role": "us_orb_watcher",
        "runtime_attestation_sha256": "d" * 64,
        "runtime_commit_sha": "c" * 40,
        "schema_version": 2,
        "source_commit_sha": source_commit,
    }


def test_frozen_runtime_runner_executes_from_clean_detached_commit(tmp_path: Path) -> None:
    # Given: an exact clean runtime is detached from mutable branch authority.
    repository = tmp_path / "frozen-runtime"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Scheduler Test")
    _git(repository, "config", "user.email", "scheduler@example.invalid")
    (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "fixture")
    source_commit = _git(repository, "rev-parse", "HEAD").stdout.strip()
    _git(repository, "checkout", "--detach", source_commit)
    receipt = tmp_path / "receipt.json"
    plist = tmp_path / "persistent.plist"
    plist.touch(mode=0o600)
    wrapper = tmp_path / "runner.zsh"
    wrapper.write_text(
        render_persistent_runner(
            OneShotRunnerSpec(
                label="ai.trading-agent.pytest-frozen-runtime",
                run_at=dt.datetime(1970, 1, 1, tzinfo=dt.UTC),
                receipt=receipt,
                command=("/usr/bin/true",),
                expires_at=dt.datetime(2100, 1, 1, tzinfo=dt.UTC),
                persistent_plist=plist,
                authority_repository=repository,
                source_commit=source_commit,
                authority_mode="frozen_runtime",
            )
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o700)

    untracked = repository / "untracked.py"
    untracked.write_text("raise RuntimeError\n", encoding="utf-8")

    # When / Then: an untracked runtime file blocks execution before the command.
    blocked = subprocess.run(
        (str(wrapper),),
        check=False,
        capture_output=True,
        text=True,
    )
    assert blocked.returncode == 78
    assert json.loads(receipt.read_text(encoding="utf-8"))["result"] == "blocked"

    untracked.unlink()
    receipt.unlink()
    plist.touch(mode=0o600)

    # When: launchd executes the clean rendered persistent wrapper.
    completed = subprocess.run(
        (str(wrapper),),
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: detached frozen authority reaches the command and records completion.
    assert completed.returncode == 0
    assert json.loads(receipt.read_text(encoding="utf-8"))["result"] == "completed"


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
