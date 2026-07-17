from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_grok_task.py"


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init"), cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(("git", "config", "user.email", "tests@example.invalid"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.name", "Tests"), cwd=repo, check=True)
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(("git", "add", ".gitignore", "README.md"), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-m", "fixture"), cwd=repo, check=True, capture_output=True, text=True)
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, head


def _write_contract(path: Path, base_commit: str) -> Path:
    contract = {
        "schema_version": 1,
        "task_id": "m4-replay-input",
        "base_commit": base_commit,
        "objective": "Private objective that must not appear in a report.",
        "allowed_paths": ["development_harness/task_contract.py"],
        "required_commands": ["uv run pytest tests/test_development_harness_task_contract.py -q"],
        "manual_qa_commands": ["uv run python run_grok_task.py --help"],
        "expected_summary_fields": ["changed_files", "verification", "concerns"],
    }
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(SCRIPT), *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_dry_run_emits_safe_plan(tmp_path: Path) -> None:
    repo, head = _init_repo(tmp_path)
    contract = _write_contract(tmp_path / "contract.json", head)

    result = _run_cli(
        "--contract",
        str(contract),
        "--worktree-root",
        str(tmp_path / "workers"),
        "--dry-run",
        cwd=repo,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "planned"
    assert payload["worktree_id"] == "m4-replay-input"
    assert "Private objective" not in result.stdout
    assert not (tmp_path / "workers").exists()


def test_cli_rejects_invalid_contract_without_echoing_contents(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    contract = tmp_path / "bad.json"
    contract.write_text('{"unsafe": "secret-like-value"}', encoding="utf-8")

    result = _run_cli(
        "--contract",
        str(contract),
        "--worktree-root",
        str(tmp_path / "workers"),
        "--dry-run",
        cwd=repo,
    )

    assert result.returncode == 1
    assert "secret-like-value" not in result.stderr


def test_cli_help_is_available() -> None:
    result = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Grok" in result.stdout


def test_cli_returns_nonzero_when_worker_fails(tmp_path: Path) -> None:
    repo, head = _init_repo(tmp_path)
    contract = _write_contract(tmp_path / "contract.json", head)
    fake_grok = tmp_path / "fake-grok"
    fake_grok.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    fake_grok.chmod(0o700)

    result = _run_cli(
        "--contract",
        str(contract),
        "--worktree-root",
        str(tmp_path / "workers"),
        "--grok-binary",
        str(fake_grok),
        cwd=repo,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "worker_failed"


def test_pyproject_includes_harness_in_basedpyright() -> None:
    pyproject = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"development_harness"' in pyproject
    assert '"run_grok_task.py"' in pyproject
