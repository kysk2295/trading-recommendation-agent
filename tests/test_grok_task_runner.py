from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from development_harness.grok_task_runner import (
    GrokTaskRunnerError,
    assert_changed_paths_allowed,
    build_grok_command,
    prepare_grok_task,
    run_grok_task,
)
from development_harness.grok_worker_process import run_worker_process
from development_harness.grok_worker_report import parse_worker_summary
from development_harness.grok_workspace_guard import (
    GrokWorkspaceGuardError,
    absolute_path_has_symlink_component,
    assert_main_repository_root,
    capture_workspace_snapshot,
    verify_workspace_snapshot,
)
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
    # Resolve so macOS temp roots are ``/private/var/...`` without ``/var`` symlink components.
    base = tmp_path.resolve()
    repo = base / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "tests@example.invalid")
    _run_git(repo, "config", "user.name", "Tests")
    (repo / ".gitignore").write_text(".worktrees/\nignored-fixture.txt\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    _run_git(repo, "add", ".gitignore", "README.md")
    _run_git(repo, "commit", "-m", "fixture")
    return repo


def _passing_commands() -> tuple[tuple[str, ...], tuple[str, ...]]:
    required = ("uv run python -c pass",)
    manual = ("uv run python -c pass",)
    return required, manual


def _verification_list() -> list[str]:
    required, manual = _passing_commands()
    return sorted(set((*required, *manual)))


def _contract(repo: Path, *, allowed_paths: tuple[str, ...] | None = None) -> GrokTaskContract:
    required, manual = _passing_commands()
    return GrokTaskContract(
        schema_version=1,
        task_id="m4-replay-input",
        base_commit=_run_git(repo, "rev-parse", "HEAD"),
        objective="Add a replay-bound research input contract.",
        allowed_paths=allowed_paths or ("development_harness/task_contract.py",),
        required_commands=required,
        manual_qa_commands=manual,
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
    concerns_schema = schema["properties"]["concerns"]
    assert concerns_schema["uniqueItems"] is True
    assert set(concerns_schema["items"]["enum"]) == {
        "timeout_risk",
        "scope_pressure",
        "test_gap",
        "docs_gap",
        "verification_gap",
        "residual_risk",
    }
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


def test_prepare_rejects_non_main_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _run_git(repo, "checkout", "-b", "feature")

    with pytest.raises(GrokTaskRunnerError, match="main"):
        _ = prepare_grok_task(_contract(repo), repo=repo, dry_run=True)


def test_prepare_rejects_symlink_repository_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    link = tmp_path / "linked-repo"
    link.symlink_to(repo, target_is_directory=True)

    with pytest.raises(GrokTaskRunnerError, match="symlink"):
        _ = prepare_grok_task(_contract(repo), repo=link, dry_run=True)


def test_prepare_rejects_linked_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = tmp_path / "linked-worktree"
    _run_git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")

    with pytest.raises(GrokTaskRunnerError, match="linked worktree"):
        _ = prepare_grok_task(_contract(repo), repo=worktree, dry_run=True)


def test_prepare_rejects_stale_contract_base(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    stale = _contract(repo).model_copy(update={"base_commit": "b" * 40})

    with pytest.raises(GrokTaskRunnerError, match="base does not match"):
        _ = prepare_grok_task(stale, repo=repo, dry_run=True)


def test_prepare_rejects_symlink_component_in_allowed_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "development_harness").mkdir()
    real = repo / "development_harness" / "real.py"
    real.write_text("real\n", encoding="utf-8")
    link = repo / "development_harness" / "task_contract.py"
    link.symlink_to(real)

    with pytest.raises(GrokTaskRunnerError, match="symlink"):
        _ = prepare_grok_task(_contract(repo), repo=repo, dry_run=True)


def test_runner_rejects_symlink_created_on_allowed_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    fake_grok = tmp_path / "fake-grok"
    verification = _verification_list()
    fake_grok.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import json",
                "from pathlib import Path",
                "target = Path('development_harness/real.py')",
                "target.parent.mkdir(parents=True, exist_ok=True)",
                "target.write_text('real\\n', encoding='utf-8')",
                "Path('development_harness/task_contract.py').symlink_to(target)",
                "print(json.dumps({",
                "  'structuredOutput': {",
                f"    'changed_files': [{allowed!r}],",
                f"    'verification': {verification!r},",
                "    'concerns': [],",
                "  }",
                "}))",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_grok.chmod(0o700)
    plan = prepare_grok_task(_contract(repo), repo=repo, grok_binary=str(fake_grok), dry_run=False)

    with pytest.raises(GrokTaskRunnerError, match="symlink"):
        _ = run_grok_task(plan, dry_run=False)


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
    commit_and_reset: bool = False,
    sleep_seconds: float | None = None,
    mutate_user_owned: str | None = None,
    mutate_ignored: str | None = None,
    spawn_child_sleep: float | None = None,
) -> Path:
    lines = [
        "#!/usr/bin/env python3",
        "import json",
        "import os",
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
    if mutate_user_owned is not None:
        lines.extend(
            (
                f"owned = Path({mutate_user_owned!r})",
                "owned.parent.mkdir(parents=True, exist_ok=True)",
                "owned.write_text('mutated-user-owned\\n', encoding='utf-8')",
            )
        )
    if mutate_ignored is not None:
        lines.extend(
            (
                f"ignored = Path({mutate_ignored!r})",
                "ignored.write_text('mutated-ignored\\n', encoding='utf-8')",
            )
        )
    if commit or commit_and_reset:
        lines.extend(
            (
                "subprocess.run(('git', 'add', '-A'), check=True)",
                "subprocess.run(('git', 'commit', '-m', 'worker commit'), check=True)",
            )
        )
    if commit_and_reset:
        lines.append("subprocess.run(('git', 'reset', '--hard', 'HEAD~1'), check=True)")
    if spawn_child_sleep is not None:
        lines.extend(
            (
                "if os.fork() == 0:",
                f"    time.sleep({spawn_child_sleep!r})",
                "    raise SystemExit(0)",
            )
        )
    if sleep_seconds is not None:
        lines.append(f"time.sleep({sleep_seconds!r})")
    default_changed = list(write_paths)
    if rename_from is not None and rename_to is not None:
        default_changed.extend([rename_from, rename_to])
    structured = summary or {
        "changed_files": default_changed,
        "verification": _verification_list(),
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
            "verification": _verification_list(),
            "concerns": ["summary_omits_real_edits"],
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
    assert "summary_omits_real_edits" not in json.dumps(safe)


def test_runner_accepts_summary_changed_files_in_any_order(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    first = "development_harness/task_contract.py"
    second = "tests/test_development_harness_task_contract.py"
    required, manual = _passing_commands()
    (repo / "development_harness").mkdir()
    (repo / "tests").mkdir()
    contract = GrokTaskContract(
        schema_version=1,
        task_id="m4-replay-input",
        base_commit=_run_git(repo, "rev-parse", "HEAD"),
        objective="Add a replay-bound research input contract.",
        allowed_paths=(first, second),
        required_commands=required,
        manual_qa_commands=manual,
        expected_summary_fields=("changed_files", "verification", "concerns"),
    )
    reverse_summary = {
        "changed_files": [second, first],
        "verification": _verification_list(),
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
            "verification": _verification_list(),
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
            "verification": _verification_list(),
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


def test_runner_timeout_kills_worker_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import development_harness.grok_task_runner as runner_module

    repo = _init_repo(tmp_path)
    monkeypatch.setattr(runner_module, "_GROK_TIMEOUT_SECONDS", 0.5)
    marker = tmp_path / "child-still-running"
    fake_grok = tmp_path / "fake-grok"
    fake_grok.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import os",
                "import time",
                "from pathlib import Path",
                f"marker = Path({str(marker)!r})",
                "if os.fork() == 0:",
                "    time.sleep(30)",
                "    marker.write_text('alive', encoding='utf-8')",
                "    raise SystemExit(0)",
                "time.sleep(30)",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_grok.chmod(0o700)
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    report = run_grok_task(plan, dry_run=False)
    time.sleep(1.0)

    assert report.status == "worker_failed"
    assert not marker.exists()


def test_runner_parses_structured_output_not_concatenated_text(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    structured = {
        "changed_files": [allowed],
        "verification": _verification_list(),
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


def test_runner_rejects_user_owned_metadata_or_content_mutation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".hermes").mkdir()
    (repo / ".hermes" / "notes.txt").write_text("user-owned\n", encoding="utf-8")
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        mutate_user_owned=".hermes/notes.txt",
        summary={
            "changed_files": [allowed],
            "verification": _verification_list(),
            "concerns": [],
        },
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    with pytest.raises(GrokTaskRunnerError, match="user-owned"):
        _ = run_grok_task(plan, dry_run=False)


def test_runner_rejects_preexisting_ignored_file_mutation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ignored = repo / "ignored-fixture.txt"
    ignored.write_text("before\n", encoding="utf-8")
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        mutate_ignored="ignored-fixture.txt",
        summary={
            "changed_files": [allowed],
            "verification": _verification_list(),
            "concerns": [],
        },
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    with pytest.raises(GrokTaskRunnerError, match="ignored"):
        _ = run_grok_task(plan, dry_run=False)


def test_runner_rejects_commit_reset_via_git_db_fingerprint(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    head_before = _run_git(repo, "rev-parse", "HEAD")
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        commit_and_reset=True,
        summary={
            "changed_files": [allowed],
            "verification": _verification_list(),
            "concerns": [],
        },
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    with pytest.raises(GrokTaskRunnerError, match="Git database"):
        _ = run_grok_task(plan, dry_run=False)

    assert _run_git(repo, "rev-parse", "HEAD") == head_before


def test_runner_rejects_summary_verification_outside_contract_commands(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
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

    report = run_grok_task(plan, dry_run=False)

    assert report.status == "worker_failed"
    assert report.summary is None


def test_runner_rejects_unsafe_summary_concern_tokens(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        summary={
            "changed_files": [allowed],
            "verification": _verification_list(),
            "concerns": ["Free-form secret leakage risk"],
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


def test_runner_reruns_required_and_manual_commands_offline_before_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import development_harness.grok_verification as verification_module

    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()
    calls: list[tuple[str, ...]] = []

    def _record(command: tuple[str, ...], **kwargs: object) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr(verification_module, "run_verification_command", _record)
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        summary={
            "changed_files": [allowed],
            "verification": _verification_list(),
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

    assert report.status == "completed"
    assert calls
    assert all(command[:3] == ("uv", "run", "--offline") for command in calls)
    assert ("uv", "run", "--offline", "python", "-c", "pass") in calls


def test_runner_fails_when_independent_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import development_harness.grok_verification as verification_module

    repo = _init_repo(tmp_path)
    allowed = "development_harness/task_contract.py"
    (repo / "development_harness").mkdir()

    def _fail(command: tuple[str, ...], **kwargs: object) -> int:
        return 2

    monkeypatch.setattr(verification_module, "run_verification_command", _fail)
    fake_grok = _fake_grok(
        tmp_path / "fake-grok",
        changed_path=allowed,
        summary={
            "changed_files": [allowed],
            "verification": _verification_list(),
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


def test_parse_worker_summary_prefers_structured_output_over_text() -> None:
    envelope = _envelope(
        {
            "changed_files": ["development_harness/task_contract.py"],
            "verification": _verification_list(),
            "concerns": [],
        },
        text=(
            '{"changed_files":["DRAFT_ONLY.md"],"verification":["draft"],"concerns":["from-text"]}\n'
            '{"changed_files":[],"verification":[],"concerns":[]}'
        ),
    )

    summary = parse_worker_summary(
        json.dumps(envelope),
        allowed_paths=frozenset({"development_harness/task_contract.py"}),
        required_verification=frozenset(_verification_list()),
    )

    assert summary is not None
    assert summary.changed_files == ("development_harness/task_contract.py",)
    assert summary.concerns == ()
    assert "DRAFT_ONLY" not in json.dumps(summary.as_safe_dict())


def test_parse_worker_summary_rejects_text_only_or_oversized_payload() -> None:
    text_only = {
        "text": json.dumps(
            {
                "changed_files": ["development_harness/task_contract.py"],
                "verification": _verification_list(),
                "concerns": [],
            }
        )
    }
    oversized = _envelope(
        {
            "changed_files": ["x" * 10_000],
            "verification": _verification_list(),
            "concerns": [],
        }
    )
    deep_payload: dict[str, object] = {"i": 1}
    for key in ("h", "g", "f", "e", "d", "c", "b", "a"):
        deep_payload = {key: deep_payload}
    deep = {"structuredOutput": deep_payload}

    assert parse_worker_summary(json.dumps(text_only)) is None
    assert parse_worker_summary(json.dumps(oversized)) is None
    assert parse_worker_summary(json.dumps(deep)) is None


def test_parse_worker_summary_handles_true_deep_nesting_without_raising() -> None:
    depth = 5_000
    deep_array = "[" * depth + "0" + "]" * depth
    deep_object = '{"structuredOutput":' + ('{"a":' * depth) + "0" + ("}" * depth) + "}"

    assert parse_worker_summary(deep_array) is None
    assert parse_worker_summary(deep_object) is None


def test_parse_worker_summary_rejects_paths_and_verification_outside_contract() -> None:
    required = frozenset(_verification_list())
    envelope = _envelope(
        {
            "changed_files": ["outside.py"],
            "verification": _verification_list(),
            "concerns": [],
        }
    )
    bad_verification = _envelope(
        {
            "changed_files": ["development_harness/task_contract.py"],
            "verification": ["uv run pytest -q"],
            "concerns": [],
        }
    )
    empty_verification = _envelope(
        {
            "changed_files": ["development_harness/task_contract.py"],
            "verification": [],
            "concerns": [],
        }
    )
    duplicate_verification = _envelope(
        {
            "changed_files": ["development_harness/task_contract.py"],
            "verification": _verification_list() + _verification_list(),
            "concerns": [],
        }
    )
    duplicate_concerns = _envelope(
        {
            "changed_files": ["development_harness/task_contract.py"],
            "verification": _verification_list(),
            "concerns": ["test_gap", "test_gap"],
        }
    )

    assert (
        parse_worker_summary(
            json.dumps(envelope),
            allowed_paths=frozenset({"development_harness/task_contract.py"}),
            required_verification=required,
        )
        is None
    )
    assert (
        parse_worker_summary(
            json.dumps(bad_verification),
            allowed_paths=frozenset({"development_harness/task_contract.py"}),
            required_verification=required,
        )
        is None
    )
    assert (
        parse_worker_summary(
            json.dumps(empty_verification),
            allowed_paths=frozenset({"development_harness/task_contract.py"}),
            required_verification=required,
        )
        is None
    )
    assert (
        parse_worker_summary(
            json.dumps(duplicate_verification),
            allowed_paths=frozenset({"development_harness/task_contract.py"}),
            required_verification=required,
        )
        is None
    )
    assert (
        parse_worker_summary(
            json.dumps(duplicate_concerns),
            allowed_paths=frozenset({"development_harness/task_contract.py"}),
            required_verification=required,
        )
        is None
    )


def test_run_worker_process_oversize_stdout_kills_group(tmp_path: Path) -> None:
    from development_harness.grok_worker_process import WorkerProcessError

    marker = tmp_path / "child-still-running-oversize"
    script = tmp_path / "noisy-oversize.py"
    script.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import os",
                "import sys",
                "import time",
                "from pathlib import Path",
                f"marker = Path({str(marker)!r})",
                "if os.fork() == 0:",
                "    time.sleep(30)",
                "    marker.write_text('alive', encoding='utf-8')",
                "    raise SystemExit(0)",
                "sys.stdout.write('x' * 200)",
                "sys.stdout.flush()",
                "time.sleep(30)",
                "",
            )
        ),
        encoding="utf-8",
    )
    script.chmod(0o700)

    with pytest.raises(WorkerProcessError, match="stdout"):
        _ = run_worker_process(
            (str(script),),
            cwd=tmp_path,
            timeout_seconds=30.0,
            max_stdout_bytes=16,
        )
    time.sleep(0.5)
    assert not marker.exists()


def test_run_worker_process_timeout_kills_group(tmp_path: Path) -> None:
    marker = tmp_path / "child-still-running-timeout"
    script = tmp_path / "noisy-timeout.py"
    script.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import os",
                "import time",
                "from pathlib import Path",
                f"marker = Path({str(marker)!r})",
                "if os.fork() == 0:",
                "    time.sleep(30)",
                "    marker.write_text('alive', encoding='utf-8')",
                "    raise SystemExit(0)",
                "time.sleep(30)",
                "",
            )
        ),
        encoding="utf-8",
    )
    script.chmod(0o700)

    with pytest.raises(TimeoutError):
        _ = run_worker_process(
            (str(script),),
            cwd=tmp_path,
            timeout_seconds=0.3,
            max_stdout_bytes=1_048_576,
        )
    time.sleep(0.5)
    assert not marker.exists()


def test_workspace_guard_detects_main_and_snapshots(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    root = assert_main_repository_root(repo)
    snapshot = capture_workspace_snapshot(root)
    assert isinstance(snapshot.user_owned, tuple)
    assert isinstance(snapshot.ignored, tuple)
    verify_workspace_snapshot(root, snapshot)

    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    # checkout dirt is not git-db; force a ref change
    _run_git(repo, "update-ref", "refs/heads/extra", _run_git(repo, "rev-parse", "HEAD"))
    with pytest.raises(GrokWorkspaceGuardError, match="Git database"):
        verify_workspace_snapshot(root, snapshot)


def test_workspace_snapshot_rejects_local_git_config_change_without_head_move(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    root = assert_main_repository_root(repo)
    head_before = _run_git(repo, "rev-parse", "HEAD")
    snapshot = capture_workspace_snapshot(root)

    config_path = repo / ".git" / "config"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n[user]\n\tname = worker-tamper\n",
        encoding="utf-8",
    )

    with pytest.raises(GrokWorkspaceGuardError, match="Git database"):
        verify_workspace_snapshot(root, snapshot)
    assert _run_git(repo, "rev-parse", "HEAD") == head_before


def test_workspace_snapshot_rejects_hook_create_or_replace_without_head_move(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    root = assert_main_repository_root(repo)
    head_before = _run_git(repo, "rev-parse", "HEAD")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    existing = hooks / "pre-commit.sample"
    if not existing.exists():
        existing.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    snapshot = capture_workspace_snapshot(root)

    created = hooks / "pre-commit"
    created.write_text("#!/bin/sh\necho tamper\n", encoding="utf-8")
    with pytest.raises(GrokWorkspaceGuardError, match="Git database"):
        verify_workspace_snapshot(root, snapshot)

    created.unlink()
    # Replace an existing hook entry (symlink) without moving HEAD.
    existing.unlink()
    existing.symlink_to("/bin/true")
    with pytest.raises(GrokWorkspaceGuardError, match="Git database"):
        verify_workspace_snapshot(root, snapshot)
    assert _run_git(repo, "rev-parse", "HEAD") == head_before


def test_workspace_snapshot_uses_metadata_without_reading_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".hermes").mkdir()
    notes = repo / ".hermes" / "notes.txt"
    notes.write_text("user-owned\n", encoding="utf-8")
    ignored = repo / "ignored-fixture.txt"
    ignored.write_text("ignored\n", encoding="utf-8")

    def _forbid_read(self: Path, *args: object, **kwargs: object) -> bytes:
        raise AssertionError("snapshot must not read file contents")

    monkeypatch.setattr(Path, "read_bytes", _forbid_read)
    monkeypatch.setattr(Path, "read_text", _forbid_read)
    snapshot = capture_workspace_snapshot(repo)
    verify_workspace_snapshot(repo, snapshot)
    assert snapshot.user_owned
    assert any(path == "ignored-fixture.txt" for path, _meta in snapshot.ignored)


def test_workspace_snapshot_records_symlink_directory_once_and_detects_replacement(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".hermes").mkdir()
    target = tmp_path / "outside-target"
    target.mkdir()
    (target / "payload.txt").write_text("outside\n", encoding="utf-8")
    linked = repo / ".hermes" / "linked-dir"
    linked.symlink_to(target, target_is_directory=True)

    snapshot = capture_workspace_snapshot(repo)
    hermes_entries = dict(snapshot.user_owned)[".hermes"]
    hermes_map = dict(hermes_entries)
    assert "linked-dir" in hermes_map
    assert "linked-dir/payload.txt" not in hermes_map
    assert [path for path, _meta in hermes_entries].count("linked-dir") == 1
    verify_workspace_snapshot(repo, snapshot)

    linked.unlink()
    linked.mkdir()
    (linked / "local.txt").write_text("local\n", encoding="utf-8")
    with pytest.raises(GrokWorkspaceGuardError, match="user-owned"):
        verify_workspace_snapshot(repo, snapshot)


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
            "verification": _verification_list(),
            "concerns": [],
        },
    )
    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )

    with pytest.raises(GrokTaskRunnerError, match=r"worker committed|Git database|HEAD"):
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


@pytest.mark.parametrize("flag_args", (("--assume-unchanged",), ("--skip-worktree",)))
def test_workspace_snapshot_rejects_index_flag_changes(
    tmp_path: Path,
    flag_args: tuple[str, ...],
) -> None:
    repo = _init_repo(tmp_path)
    root = assert_main_repository_root(repo)
    snapshot = capture_workspace_snapshot(root)

    _run_git(repo, "update-index", *flag_args, "README.md")

    with pytest.raises(GrokWorkspaceGuardError, match="index"):
        verify_workspace_snapshot(root, snapshot)


def test_workspace_snapshot_includes_object_symlinks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    root = assert_main_repository_root(repo)
    snapshot = capture_workspace_snapshot(root)

    objects = repo / ".git" / "objects" / "zz"
    objects.mkdir(parents=True, exist_ok=True)
    (objects / "symlink-entry").symlink_to("/tmp/outside-object-target")

    with pytest.raises(GrokWorkspaceGuardError, match="Git database"):
        verify_workspace_snapshot(root, snapshot)


def test_workspace_snapshot_inventories_empty_ignored_directories(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    gitignore = repo / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8") + "empty-ignored/\n", encoding="utf-8")
    _run_git(repo, "add", ".gitignore")
    _run_git(repo, "commit", "-m", "ignore empty dir")
    empty = repo / "empty-ignored"
    empty.mkdir()

    snapshot = capture_workspace_snapshot(repo)

    assert any(path.rstrip("/") == "empty-ignored" for path, _meta in snapshot.ignored)
    verify_workspace_snapshot(repo, snapshot)

    empty.rmdir()
    with pytest.raises(GrokWorkspaceGuardError, match="ignored"):
        verify_workspace_snapshot(repo, snapshot)


def test_prepare_rejects_symlink_component_in_repository_path(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    base = root / "base"
    base.mkdir()
    repo = _init_repo(base)
    link = root / "via-link"
    link.symlink_to(base, target_is_directory=True)
    linked_repo = link / "repo"

    with pytest.raises(GrokTaskRunnerError, match="symlink"):
        _ = prepare_grok_task(_contract(repo), repo=linked_repo, dry_run=True)


def test_offline_verification_disables_caches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import development_harness.grok_verification as verification_module

    captured: dict[str, object] = {}

    def _record(command: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["env"] = kwargs.get("env")
        captured["command"] = command
        return subprocess.CompletedProcess(command if isinstance(command, list) else [], 0)

    monkeypatch.setattr(subprocess, "run", _record)
    code = verification_module.run_verification_command(
        verification_module.offline_command("uv run ruff check development_harness"),
        cwd=tmp_path,
    )

    assert code == 0
    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("PYTHONDONTWRITEBYTECODE") == "1"
    assert "RUFF_NO_CACHE" not in env
    assert env.get("PYTEST_ADDOPTS", "").split() == ["-p", "no:cacheprovider"]
    assert captured["command"] == (
        "uv",
        "run",
        "--offline",
        "ruff",
        "check",
        "--no-cache",
        "development_harness",
    )


def test_cache_disabled_environ_replaces_inherited_pytest_addopts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from development_harness.grok_verification import cache_disabled_environ

    monkeypatch.setenv("PYTEST_ADDOPTS", "-q -p cacheprovider --tb=short -p myplugin")
    monkeypatch.setenv("PATH", "/usr/bin")
    first = cache_disabled_environ()
    second = cache_disabled_environ(base=first)
    assert first["PYTEST_ADDOPTS"] == "-p no:cacheprovider"
    assert second["PYTEST_ADDOPTS"] == "-p no:cacheprovider"
    # Unrelated keys are preserved; inherited pytest options cannot re-enable cache.
    assert first["PATH"] == "/usr/bin"
    assert "cacheprovider" not in first["PYTEST_ADDOPTS"].replace("no:cacheprovider", "")
    assert "myplugin" not in first["PYTEST_ADDOPTS"]


def test_cache_safe_and_offline_commands_inject_ruff_no_cache_idempotently() -> None:
    from development_harness.grok_verification import cache_safe_command, offline_command

    base = "uv run ruff check development_harness tests"
    already = "uv run ruff check --no-cache development_harness tests"
    assert cache_safe_command(base) == "uv run ruff check --no-cache development_harness tests"
    assert cache_safe_command(already) == already
    assert offline_command(base) == (
        "uv",
        "run",
        "--offline",
        "ruff",
        "check",
        "--no-cache",
        "development_harness",
        "tests",
    )
    assert offline_command(already) == (
        "uv",
        "run",
        "--offline",
        "ruff",
        "check",
        "--no-cache",
        "development_harness",
        "tests",
    )


def test_cache_disabled_environ_is_shared_by_worker_and_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from development_harness.grok_verification import cache_disabled_environ

    expected = cache_disabled_environ()
    assert expected.get("PYTHONDONTWRITEBYTECODE") == "1"
    assert "RUFF_NO_CACHE" not in expected
    assert expected.get("PYTEST_ADDOPTS", "").split() == ["-p", "no:cacheprovider"]

    probe = tmp_path.resolve() / "env-probe.py"
    probe.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import json",
                "import os",
                "print(json.dumps({",
                "  'PYTHONDONTWRITEBYTECODE': os.environ.get('PYTHONDONTWRITEBYTECODE'),",
                "  'PYTEST_ADDOPTS': os.environ.get('PYTEST_ADDOPTS'),",
                "  'has_ruff_no_cache_env': 'RUFF_NO_CACHE' in os.environ,",
                "}))",
                "",
            )
        ),
        encoding="utf-8",
    )
    probe.chmod(0o700)
    worker_result = run_worker_process(
        (str(probe),),
        cwd=tmp_path.resolve(),
        timeout_seconds=5.0,
        max_stdout_bytes=4_096,
    )
    worker_env = json.loads(worker_result.stdout.decode())
    assert worker_env["PYTHONDONTWRITEBYTECODE"] == expected["PYTHONDONTWRITEBYTECODE"]
    assert worker_env["PYTEST_ADDOPTS"] == expected["PYTEST_ADDOPTS"]
    assert worker_env["has_ruff_no_cache_env"] is False

    captured: dict[str, object] = {}

    def _record(command: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command if isinstance(command, list) else [], 0)

    import development_harness.grok_verification as verification_module

    monkeypatch.setattr(subprocess, "run", _record)
    _ = verification_module.run_verification_command(
        ("uv", "run", "--offline", "python", "-c", "pass"),
        cwd=tmp_path.resolve(),
    )
    verification_env = captured["env"]
    assert isinstance(verification_env, dict)
    assert verification_env.get("PYTHONDONTWRITEBYTECODE") == expected["PYTHONDONTWRITEBYTECODE"]
    assert verification_env.get("PYTEST_ADDOPTS") == expected["PYTEST_ADDOPTS"]
    assert "RUFF_NO_CACHE" not in verification_env


def test_worker_prompt_shows_ruff_no_cache(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    contract = _contract(repo).model_copy(
        update={
            "required_commands": ("uv run ruff check development_harness",),
            "manual_qa_commands": ("uv run python -c pass",),
        }
    )
    plan = prepare_grok_task(contract, repo=repo, dry_run=True)
    assert "uv run ruff check --no-cache development_harness" in plan.prompt
    assert "uv run ruff check development_harness\n" not in plan.prompt.replace(
        "uv run ruff check --no-cache development_harness", ""
    )


def test_actual_ruff_offline_command_leaves_no_ruff_cache(tmp_path: Path) -> None:
    from development_harness.grok_verification import offline_command, run_verification_command

    repo = tmp_path.resolve() / "ruff-repo"
    repo.mkdir()
    (repo / "sample.py").write_text("x = 1\n", encoding="utf-8")
    command = offline_command("uv run ruff check sample.py")
    assert "--no-cache" in command
    code = run_verification_command(command, cwd=repo)
    assert code == 0
    assert not (repo / ".ruff_cache").exists()


def test_worker_subprocess_cache_disabled_env_blocks_ignored_cache_mutation(
    tmp_path: Path,
) -> None:
    """Integration: a real worker child under the harness env cannot leave cache dirt."""

    repo = _init_repo(tmp_path)
    gitignore = repo / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8")
        + "__pycache__/\n*.py[cod]\n.pytest_cache/\n.ruff_cache/\n",
        encoding="utf-8",
    )
    _run_git(repo, "add", ".gitignore")
    _run_git(repo, "commit", "-m", "ignore tool caches")
    (repo / "development_harness").mkdir()
    allowed = "development_harness/task_contract.py"
    verification = ["uv run python -c pass"]

    # Simulates a worker that would create tool caches unless the harness env disables them.
    fake_grok = tmp_path.resolve() / "cache-probe-grok"
    fake_grok.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import json",
                "import os",
                "import sys",
                "from pathlib import Path",
                "",
                "root = Path.cwd()",
                "mod = root / '_cache_probe_mod.py'",
                "mod.write_text('VALUE = 1\\n', encoding='utf-8')",
                "sys.path.insert(0, str(root))",
                "import importlib",
                "try:",
                "    importlib.invalidate_caches()",
                "    importlib.import_module('_cache_probe_mod')",
                "finally:",
                "    mod.unlink(missing_ok=True)",
                "",
                "# Mirror tool behavior: write caches only when the harness env failed to disable them.",
                "if os.environ.get('PYTHONDONTWRITEBYTECODE') != '1':",
                "    pycache = root / '__pycache__'",
                "    pycache.mkdir(exist_ok=True)",
                "    (pycache / '_cache_probe_mod.cpython-fake.pyc').write_bytes(b'fake')",
                "tokens = os.environ.get('PYTEST_ADDOPTS', '').split()",
                "has_pair = any(",
                "    tokens[i] == '-p' and tokens[i + 1] == 'no:cacheprovider'",
                "    for i in range(len(tokens) - 1)",
                ")",
                "if not has_pair:",
                "    pytest_cache = root / '.pytest_cache' / 'v' / 'cache'",
                "    pytest_cache.mkdir(parents=True, exist_ok=True)",
                "    (pytest_cache / 'nodeids').write_text('[]\\n', encoding='utf-8')",
                "",
                f"target = Path({allowed!r})",
                "target.parent.mkdir(parents=True, exist_ok=True)",
                "target.write_text('worker change\\n', encoding='utf-8')",
                "print(json.dumps({",
                "  'structuredOutput': {",
                f"    'changed_files': [{allowed!r}],",
                f"    'verification': {verification!r},",
                "    'concerns': [],",
                "  }",
                "}))",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_grok.chmod(0o700)

    plan = prepare_grok_task(
        _contract(repo),
        repo=repo,
        grok_binary=str(fake_grok),
        dry_run=False,
    )
    report = run_grok_task(plan, dry_run=False)

    assert report.status == "completed"
    assert not (repo / "__pycache__").exists()
    assert not (repo / ".pytest_cache").exists()
    assert not (repo / ".ruff_cache").exists()
    assert not (repo / "_cache_probe_mod.py").exists()


def test_absolute_path_rejects_every_symlink_component_including_parent(
    tmp_path: Path,
) -> None:
    base = tmp_path.resolve()
    real = base / "real"
    real.mkdir()
    (real / "child").mkdir()
    parent_link = base / "parent-link"
    parent_link.symlink_to(real, target_is_directory=True)

    assert absolute_path_has_symlink_component(real / "child") is False
    assert absolute_path_has_symlink_component(parent_link) is True
    assert absolute_path_has_symlink_component(parent_link / "child") is True
    # Resolved macOS-style temp path has no symlink components.
    assert absolute_path_has_symlink_component(base) is False


def test_workspace_snapshot_detects_nested_ignored_directory_mode_change(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    gitignore = repo / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8") + "ignored-tree/\n",
        encoding="utf-8",
    )
    _run_git(repo, "add", ".gitignore")
    _run_git(repo, "commit", "-m", "ignore tree")
    nested = repo / "ignored-tree" / "nested"
    nested.mkdir(parents=True)
    snapshot = capture_workspace_snapshot(repo)
    assert any(path == "ignored-tree/nested" for path, _meta in snapshot.ignored)

    nested.chmod(0o700)
    with pytest.raises(GrokWorkspaceGuardError, match="ignored"):
        verify_workspace_snapshot(repo, snapshot)


def test_workspace_guard_reexports_fingerprint_snapshot_api(tmp_path: Path) -> None:
    """Public guard imports remain stable after fingerprint extraction."""

    import development_harness.grok_workspace_fingerprint as fingerprint
    import development_harness.grok_workspace_guard as guard

    repo = _init_repo(tmp_path)
    assert guard.capture_workspace_snapshot is fingerprint.capture_workspace_snapshot
    assert guard.verify_workspace_snapshot is fingerprint.verify_workspace_snapshot
    assert guard.WorkspaceSnapshot is fingerprint.WorkspaceSnapshot
    assert guard.GrokWorkspaceGuardError is fingerprint.GrokWorkspaceGuardError
    snapshot = guard.capture_workspace_snapshot(repo)
    assert isinstance(snapshot, fingerprint.WorkspaceSnapshot)
    fingerprint.verify_workspace_snapshot(repo, snapshot)
