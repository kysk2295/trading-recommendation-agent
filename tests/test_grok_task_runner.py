from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from development_harness.grok_task_runner import (
    GrokTaskRunnerError,
    assert_changed_paths_allowed,
    prepare_grok_task,
    run_grok_task,
)
from development_harness.task_contract import GrokTaskContract


def _run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "tests@example.invalid")
    _run_git(repo, "config", "user.name", "Tests")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    _run_git(repo, "add", ".gitignore", "README.md")
    _run_git(repo, "commit", "-m", "fixture")
    return repo


def _contract(repo: Path) -> GrokTaskContract:
    return GrokTaskContract(
        schema_version=1,
        task_id="m4-replay-input",
        base_commit=_run_git(repo, "rev-parse", "HEAD"),
        objective="Add a replay-bound research input contract.",
        allowed_paths=("development_harness/task_contract.py",),
        required_commands=("uv run pytest tests/test_development_harness_task_contract.py -q",),
        manual_qa_commands=("uv run python run_grok_task.py --help",),
        expected_summary_fields=("changed_files", "verification", "concerns"),
    )


def test_prepare_dry_run_creates_no_worktree_and_returns_planned_command(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree_root = tmp_path / "workers"

    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        worktree_root=worktree_root,
        grok_binary="grok",
        dry_run=True,
    )

    assert plan.worktree_path == worktree_root / "m4-replay-input"
    assert "--sandbox" in plan.command
    assert "strict" in plan.command
    assert "--no-subagents" in plan.command
    assert "acceptEdits" in plan.command
    assert "Bash(ls *)" in plan.command
    assert "Bash(find *)" in plan.command
    assert not plan.worktree_path.exists()


def test_prepare_rejects_dirty_checkout_outside_user_owned_hermes_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(GrokTaskRunnerError, match="checkout contains changes"):
        _ = prepare_grok_task(_contract(repo), repo=repo, worktree_root=tmp_path / "workers", dry_run=True)


def test_prepare_allows_preexisting_untracked_hermes_directory(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".hermes").mkdir()
    (repo / ".hermes" / "notes.txt").write_text("user-owned\n", encoding="utf-8")

    plan = prepare_grok_task(_contract(repo), repo=repo, worktree_root=tmp_path / "workers", dry_run=True)

    assert plan.task_id == "m4-replay-input"


def test_prepare_rejects_stale_contract_base_and_existing_destination(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    stale = _contract(repo).model_copy(update={"base_commit": "b" * 40})
    destination = tmp_path / "workers" / "m4-replay-input"
    destination.mkdir(parents=True)

    with pytest.raises(GrokTaskRunnerError, match="base does not match"):
        _ = prepare_grok_task(stale, repo=repo, worktree_root=tmp_path / "workers", dry_run=True)
    with pytest.raises(GrokTaskRunnerError, match="destination already exists"):
        _ = prepare_grok_task(_contract(repo), repo=repo, worktree_root=tmp_path / "workers", dry_run=True)


def test_changed_paths_rejects_path_outside_contract() -> None:
    with pytest.raises(GrokTaskRunnerError, match="worker changed a path outside the contract"):
        assert_changed_paths_allowed(("README.md",), ("development_harness/task_contract.py",))


def _fake_grok(path: Path, *, changed_path: str | None, exit_code: int = 0) -> Path:
    lines = ["#!/usr/bin/env python3", "import json", "from pathlib import Path", ""]
    if changed_path is not None:
        lines.extend(
            (
                f"target = Path({changed_path!r})",
                "target.parent.mkdir(parents=True, exist_ok=True)",
                "target.write_text('worker change\\n', encoding='utf-8')",
            )
        )
    lines.extend(
        (
            "print(json.dumps({'text': json.dumps({'changed_files': [], 'verification': 'passed', 'concerns': []})}))",
            f"raise SystemExit({exit_code})",
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_runner_rejects_worker_change_outside_contract(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    fake_grok = _fake_grok(tmp_path / "fake-grok", changed_path="README.md")
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        worktree_root=tmp_path / "workers",
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    with pytest.raises(GrokTaskRunnerError, match="worker changed a path outside the contract"):
        _ = run_grok_task(plan, dry_run=False)


def test_runner_reports_nonzero_worker_without_exposing_output(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    fake_grok = _fake_grok(tmp_path / "fake-grok", changed_path=None, exit_code=7)
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        worktree_root=tmp_path / "workers",
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    report = run_grok_task(plan, dry_run=False)

    assert report.status == "worker_failed"
    assert report.worker_exit_code == 7
    assert "output" not in report.as_safe_dict()


def test_runner_handles_missing_grok_binary_as_a_safe_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        worktree_root=tmp_path / "workers",
        grok_binary="does-not-exist-grok",
        dry_run=False,
    )

    report = run_grok_task(plan, dry_run=False)

    assert report.status == "worker_failed"
    assert report.worker_exit_code is None
