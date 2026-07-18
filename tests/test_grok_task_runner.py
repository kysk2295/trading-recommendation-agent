from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from development_harness.grok_task_runner import (
    GrokTaskRunnerError,
    assert_changed_paths_allowed,
    build_grok_command,
    prepare_grok_task,
    run_grok_task,
)
from development_harness.grok_worker_report import parse_worker_summary
from development_harness.task_contract import GrokTaskContract, InvalidGrokTaskContractError


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


def test_prepare_dry_run_stays_in_repo_root_and_builds_bounded_command(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary="grok",
        dry_run=True,
    )

    assert plan.repository == repo.resolve()
    assert not hasattr(plan, "worktree_path") or getattr(plan, "worktree_path", None) is None
    assert not hasattr(plan, "branch_name") or getattr(plan, "branch_name", None) is None
    assert plan.command[0] == "grok"
    assert "--cwd" in plan.command
    assert str(repo.resolve()) in plan.command
    assert "--always-approve" in plan.command
    assert "--permission-mode" in plan.command
    assert "bypassPermissions" in plan.command
    assert "-p" in plan.command
    assert "--output-format" in plan.command
    assert "json" in plan.command
    assert "--json-schema" in plan.command
    schema_index = plan.command.index("--json-schema") + 1
    schema = json.loads(plan.command[schema_index])
    assert schema["required"] == ["changed_files", "verification", "concerns"]
    assert schema["additionalProperties"] is False
    assert "--no-plan" in plan.command
    assert "--no-subagents" in plan.command
    assert "--disable-web-search" in plan.command
    assert "--no-memory" in plan.command
    assert "--max-turns" in plan.command
    assert "--sandbox" not in plan.command
    assert "strict" not in plan.command
    assert "acceptEdits" not in plan.command
    assert not any(part.startswith("Bash(") for part in plan.command)
    assert "--worktree" not in plan.command
    assert _run_git(repo, "status", "--porcelain=v1") == ""


def test_build_grok_command_uses_single_turn_prompt_without_sandbox(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    contract = _contract(repo)
    command = build_grok_command(
        contract,
        grok_binary="grok",
        repository=repo,
        prompt="bounded prompt",
    )

    assert command[command.index("-p") + 1] == "bounded prompt"
    assert "--permission-mode" in command and "bypassPermissions" in command
    assert "--json-schema" in command
    assert "--sandbox" not in command


def test_prepare_rejects_dirty_checkout_outside_user_owned_state(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(GrokTaskRunnerError, match="checkout contains changes"):
        _ = prepare_grok_task(_contract(repo), repo=repo, dry_run=True)


def test_prepare_allows_preexisting_untracked_hermes_and_omo_directories(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".hermes").mkdir()
    (repo / ".hermes" / "notes.txt").write_text("user-owned\n", encoding="utf-8")
    (repo / ".omo").mkdir()
    (repo / ".omo" / "state.txt").write_text("user-owned\n", encoding="utf-8")

    plan = prepare_grok_task(_contract(repo), repo=repo, dry_run=True)

    assert plan.task_id == "m4-replay-input"
    assert plan.repository == repo.resolve()


def test_prepare_rejects_stale_contract_base(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    stale = _contract(repo).model_copy(update={"base_commit": "b" * 40})

    with pytest.raises(GrokTaskRunnerError, match="base does not match"):
        _ = prepare_grok_task(stale, repo=repo, dry_run=True)


def test_changed_paths_rejects_path_outside_contract() -> None:
    with pytest.raises(GrokTaskRunnerError, match="worker changed a path outside the contract"):
        assert_changed_paths_allowed(("README.md",), ("development_harness/task_contract.py",))


def _envelope(structured: dict[str, object], *, text: str = "") -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "uuid": "00000000-0000-4000-8000-000000000001",
        "session_id": "session-test",
        "text": text,
        "structuredOutput": structured,
    }


def _fake_grok(
    path: Path,
    *,
    changed_path: str | None = None,
    changed_paths: tuple[str, ...] | None = None,
    rename_from: str | None = None,
    rename_to: str | None = None,
    exit_code: int = 0,
    summary: dict[str, object] | None = None,
    text: str | None = None,
    commit: bool = False,
    sleep_seconds: float | None = None,
) -> Path:
    lines = [
        "#!/usr/bin/env python3",
        "import json",
        "import subprocess",
        "import time",
        "from pathlib import Path",
        "",
    ]
    write_paths: list[str] = []
    if changed_paths is not None:
        write_paths.extend(changed_paths)
    elif changed_path is not None:
        write_paths.append(changed_path)
    for write_path in write_paths:
        lines.extend(
            (
                f"target = Path({write_path!r})",
                "target.parent.mkdir(parents=True, exist_ok=True)",
                "target.write_text('worker change\\n', encoding='utf-8')",
            )
        )
    if rename_from is not None and rename_to is not None:
        lines.extend(
            (
                f"source = Path({rename_from!r})",
                f"destination = Path({rename_to!r})",
                "destination.parent.mkdir(parents=True, exist_ok=True)",
                "source.rename(destination)",
            )
        )
    if commit:
        lines.extend(
            (
                "subprocess.run(('git', 'add', '-A'), check=True)",
                "subprocess.run(('git', 'commit', '-m', 'worker commit'), check=True)",
            )
        )
    if sleep_seconds is not None:
        lines.append(f"time.sleep({sleep_seconds!r})")
    default_changed = list(write_paths)
    if rename_from is not None and rename_to is not None:
        default_changed.extend([rename_from, rename_to])
    structured = summary or {
        "changed_files": default_changed,
        "verification": ["uv run pytest -q"],
        "concerns": [],
    }
    draft_text = text if text is not None else (
        '{"changed_files":["DRAFT_ONLY.md"],"verification":["draft"],"concerns":["from-text"]}\n'
        '{"changed_files":[],"verification":[],"concerns":[]}'
    )
    envelope = _envelope(structured, text=draft_text)
    lines.extend(
        (
            f"print(json.dumps({envelope!r}))",
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
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    report = run_grok_task(plan, dry_run=False)
    safe = report.as_safe_dict()

    assert report.status == "worker_failed"
    assert report.worker_exit_code == 7
    assert "output" not in safe
    assert "stdout" not in safe
    assert "stderr" not in safe
    assert "prompt" not in safe
    assert "objective" not in safe
    assert str(repo.resolve()) not in json.dumps(safe)


def test_runner_handles_missing_grok_binary_as_a_safe_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary="does-not-exist-grok",
        dry_run=False,
    )

    report = run_grok_task(plan, dry_run=False)

    assert report.status == "worker_failed"
    assert report.worker_exit_code is None


def test_runner_rejects_mismatched_structured_changed_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        summary={
            "changed_files": [],
            "verification": ["uv run pytest -q"],
            "concerns": ["summary omits real edits"],
        },
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    report = run_grok_task(plan, dry_run=False)
    safe = report.as_safe_dict()

    assert report.status == "worker_failed"
    assert report.changed_paths == (allowed,)
    assert report.summary is None
    assert safe["summary"] is None
    assert "summary omits real edits" not in json.dumps(safe)


def test_runner_accepts_summary_changed_files_in_any_order(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    first = "development_harness/task_contract.py"
    second = "tests/test_development_harness_task_contract.py"
    (repo / "development_harness").mkdir()
    (repo / "tests").mkdir()
    contract = GrokTaskContract(
        schema_version=1,
        task_id="m4-replay-input",
        base_commit=_run_git(repo, "rev-parse", "HEAD"),
        objective="Add a replay-bound research input contract.",
        allowed_paths=(first, second),
        required_commands=("uv run pytest tests/test_development_harness_task_contract.py -q",),
        manual_qa_commands=("uv run python run_grok_task.py --help",),
        expected_summary_fields=("changed_files", "verification", "concerns"),
    )
    reverse_summary = {
        "changed_files": [second, first],
        "verification": ["uv run pytest -q"],
        "concerns": [],
    }
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_paths=(first, second),
        summary=reverse_summary,
    )
    plan = prepare_grok_task(contract, repo=repo, grok_binary=str(fake_grok), dry_run=False)

    report = run_grok_task(plan, dry_run=False)
    safe = report.as_safe_dict()

    assert report.status == "completed"
    assert report.changed_paths == tuple(sorted((first, second)))
    assert safe["summary"] == reverse_summary
    assert report.summary is not None
    assert report.summary.changed_files == (second, first)


def test_runner_rejects_duplicate_summary_changed_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        summary={
            "changed_files": [allowed, allowed],
            "verification": ["uv run pytest -q"],
            "concerns": [],
        },
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    report = run_grok_task(plan, dry_run=False)

    assert report.status == "worker_failed"
    assert report.summary is None
    assert report.as_safe_dict()["summary"] is None


def test_runner_rejects_rename_from_allowed_path_to_out_of_contract_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    outside = "outside_contract.py"
    (repo / "development_harness").mkdir()
    (repo / allowed).write_text("tracked\n", encoding="utf-8")
    _run_git(repo, "add", allowed)
    _run_git(repo, "commit", "-m", "track allowed path")
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        rename_from=allowed,
        rename_to=outside,
        summary={
            "changed_files": [allowed],
            "verification": ["uv run pytest -q"],
            "concerns": [],
        },
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    with pytest.raises(GrokTaskRunnerError, match="worker changed a path outside the contract"):
        _ = run_grok_task(plan, dry_run=False)


def test_runner_timeout_enforces_allow_list_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import development_harness.grok_task_runner as runner_module

    repo = _init_repo(tmp_path)
    monkeypatch.setattr(runner_module, "_GROK_TIMEOUT_SECONDS", 1.0)
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path="README.md",
        sleep_seconds=5.0,
        summary={
            "changed_files": ["README.md"],
            "verification": [],
            "concerns": [],
        },
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    with pytest.raises(GrokTaskRunnerError, match="worker changed a path outside the contract"):
        _ = run_grok_task(plan, dry_run=False)


def test_runner_parses_structured_output_not_concatenated_text(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    structured = {
        "changed_files": [allowed],
        "verification": ["uv run pytest tests/test_development_harness_task_contract.py -q"],
        "concerns": [],
    }
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        summary=structured,
        text=(
            '{"changed_files":["DRAFT_ONLY.md"],"verification":["draft"],"concerns":["from-text"]}\n'
            '{"changed_files":["also-draft.py"],"verification":[],"concerns":["ignore-me"]}'
        ),
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    report = run_grok_task(plan, dry_run=False)
    safe = report.as_safe_dict()
    rendered = json.dumps(safe)

    assert report.status == "completed"
    assert report.changed_paths == (allowed,)
    assert safe["summary"] == structured
    assert "DRAFT_ONLY.md" not in rendered
    assert "from-text" not in rendered
    assert "also-draft.py" not in rendered
    assert "ignore-me" not in rendered
    assert "worktree_id" not in safe
    commits = _run_git(repo, "rev-list", "--count", "HEAD")
    assert commits == "1"


def test_parse_worker_summary_prefers_structured_output_over_text() -> None:
    envelope = _envelope(
        {
            "changed_files": ["development_harness/task_contract.py"],
            "verification": ["uv run pytest -q"],
            "concerns": [],
        },
        text=(
            '{"changed_files":["DRAFT_ONLY.md"],"verification":["draft"],"concerns":["from-text"]}\n'
            '{"changed_files":[],"verification":[],"concerns":[]}'
        ),
    )

    summary = parse_worker_summary(json.dumps(envelope))

    assert summary is not None
    assert summary.changed_files == ("development_harness/task_contract.py",)
    assert summary.concerns == ()
    assert "DRAFT_ONLY" not in json.dumps(summary.as_safe_dict())


def test_parse_worker_summary_rejects_text_only_or_oversized_payload() -> None:
    text_only = {
        "text": json.dumps(
            {
                "changed_files": ["development_harness/task_contract.py"],
                "verification": ["uv run pytest -q"],
                "concerns": [],
            }
        )
    }
    oversized = _envelope(
        {
            "changed_files": ["x" * 10_000],
            "verification": ["ok"],
            "concerns": [],
        }
    )

    assert parse_worker_summary(json.dumps(text_only)) is None
    assert parse_worker_summary(json.dumps(oversized)) is None


def test_runner_rejects_worker_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    head_before = _run_git(repo, "rev-parse", "HEAD")
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        commit=True,
        summary={
            "changed_files": [allowed],
            "verification": ["uv run pytest -q"],
            "concerns": [],
        },
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    with pytest.raises(GrokTaskRunnerError, match="worker committed"):
        _ = run_grok_task(plan, dry_run=False)

    assert _run_git(repo, "rev-parse", "HEAD") != head_before


def test_contract_requires_exact_expected_summary_fields() -> None:
    payload = {
        "schema_version": 1,
        "task_id": "m4-replay-input",
        "base_commit": "a" * 40,
        "objective": "Add a replay-bound research input contract.",
        "allowed_paths": ("development_harness/task_contract.py",),
        "required_commands": ("uv run pytest tests/test_development_harness_task_contract.py -q",),
        "manual_qa_commands": ("uv run python run_grok_task.py --help",),
        "expected_summary_fields": ("changed_files", "verification"),
    }

    with pytest.raises(InvalidGrokTaskContractError):
        _ = GrokTaskContract.model_validate(payload)


def test_dry_run_does_not_invoke_worker_or_change_git(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    head_before = _run_git(repo, "rev-parse", "HEAD")
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(tmp_path / "must-not-run"),
        dry_run=True,
    )

    report = run_grok_task(plan, dry_run=True)

    assert report.status == "planned"
    assert report.worker_exit_code is None
    assert report.changed_paths == ()
    assert report.as_safe_dict().get("summary") is None
    assert _run_git(repo, "rev-parse", "HEAD") == head_before
    assert _run_git(repo, "status", "--porcelain=v1") == ""
    assert not (tmp_path / "must-not-run").exists()
