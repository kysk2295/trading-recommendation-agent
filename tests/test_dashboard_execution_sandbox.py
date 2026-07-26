from __future__ import annotations

import datetime as dt
import os
import subprocess
from pathlib import Path

import pytest

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1, trigger_fixture
from trading_agent.dashboard_execution_sandbox import (
    AutonomousExecutionSandbox,
    InvalidExecutableBindingError,
)
from trading_agent.dashboard_worktree_executor import IsolatedWorktreeExecutor


def _trigger() -> AutonomousTriggerV1:
    return AutonomousTriggerV1.model_validate(trigger_fixture(now=dt.datetime.now(dt.UTC)))


def _roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    task_root = tmp_path / "task"
    experiment = task_root / "experiment"
    worktree = task_root / "worktree"
    source = tmp_path / "source"
    for path in (experiment, worktree, source):
        path.mkdir(mode=0o700, parents=True)
    return task_root, experiment, worktree, source


def _sandbox(
    source: Path,
    *,
    hermes: Path,
    tools: tuple[Path, ...] = (),
) -> AutonomousExecutionSandbox:
    return AutonomousExecutionSandbox(
        repository=Path(__file__).resolve().parents[1],
        source_evidence_root=source,
        hermes_executable=hermes,
        fixture_mode=True,
        allowed_tool_executables=tools,
    )


def test_real_sandbox_runs_pinned_fake_hermes_and_explicit_tool_broker(tmp_path: Path) -> None:
    # Given: exact regular-file bindings for fake Hermes and one harmless broker
    repository = Path(__file__).resolve().parents[1]
    hermes = repository / "tests" / "fixtures" / "dashboard" / "fake_hermes"
    broker = repository / "tests" / "fixtures" / "dashboard" / "fake_tool_broker"
    task_root, experiment, worktree, source = _roots(tmp_path)
    sandbox = _sandbox(source, hermes=hermes, tools=(broker,))
    trigger = _trigger()
    environment = sandbox.environment(trigger, experiment)
    hermes_argv = sandbox.argv((str(hermes), "-z", "fixture"), task_root, worktree)
    broker_argv = sandbox.argv((str(broker), "probe"), task_root, worktree)
    profile = hermes_argv[2]
    assert "(allow process*)" not in profile
    assert '(subpath "/bin")' not in profile
    assert '(subpath "/usr/bin")' not in profile
    assert f'(allow process-exec (literal "{hermes.resolve()}"))' in profile
    assert '(allow process-exec (literal "/bin/zsh"))' in profile

    # When: both commands cross the real macOS sandbox-exec boundary
    hermes_result = subprocess.run(
        hermes_argv,
        cwd=worktree,
        env=environment,
        check=False,
        capture_output=True,
    )
    broker_result = subprocess.run(
        broker_argv,
        cwd=worktree,
        env=environment,
        check=False,
        capture_output=True,
    )

    # Then: pinned legitimate execution succeeds without a shell child
    assert hermes_result.returncode == 0
    assert hermes_result.stdout == b"candidate evidence recorded\n"
    assert (experiment / "candidate.json").read_text() == '{"candidate":"verified"}\n'
    assert broker_result.returncode == 0
    assert broker_result.stdout == b"broker-ok:probe\n"


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("shell", "executable_not_registered"),
        ("env-shell", "executable_not_registered"),
        ("symlink", "executable_symlink_forbidden"),
        ("traversal", "executable_path_traversal"),
        ("unregistered", "executable_not_registered"),
    ],
)
def test_unregistered_or_aliased_commands_are_typed_blockers_without_mutation(
    tmp_path: Path,
    case: str,
    expected: str,
) -> None:
    # Given: a sandbox with one exact broker binding and a host canary
    repository = Path(__file__).resolve().parents[1]
    hermes = repository / "tests" / "fixtures" / "dashboard" / "fake_hermes"
    broker = repository / "tests" / "fixtures" / "dashboard" / "fake_tool_broker"
    task_root, _, worktree, source = _roots(tmp_path)
    sandbox = _sandbox(source, hermes=hermes, tools=(broker,))
    canary = tmp_path / "canary"
    canary.write_text("unchanged")
    alias = tmp_path / "broker-link"
    alias.symlink_to(broker)
    commands = {
        "shell": ("/bin/sh", "-c", f"echo changed > {canary}"),
        "env-shell": ("/usr/bin/env", "sh", "-c", f"echo changed > {canary}"),
        "symlink": (str(alias), "probe"),
        "traversal": (str(broker.parent / ".." / "dashboard" / broker.name), "probe"),
        "unregistered": ("/usr/bin/true",),
    }

    # When: an unregistered or aliased executable is requested
    with pytest.raises(InvalidExecutableBindingError, match=expected):
        sandbox.argv(commands[case], task_root, worktree)

    # Then: the typed blocker occurs before stdout or host mutation
    assert canary.read_text() == "unchanged"


def test_hardlink_binding_and_post_preflight_substitution_are_rejected(tmp_path: Path) -> None:
    # Given: a hardlinked tool and a separately pinned script
    repository = Path(__file__).resolve().parents[1]
    hermes = repository / "tests" / "fixtures" / "dashboard" / "fake_hermes"
    broker = tmp_path / "broker"
    broker.write_text("#!/bin/zsh\nprint -r -- original\n")
    broker.chmod(0o700)
    hardlink = tmp_path / "broker-hardlink"
    os.link(broker, hardlink)
    task_root, experiment, worktree, source = _roots(tmp_path)
    hardlink_sandbox = _sandbox(source, hermes=hermes, tools=(hardlink,))
    assert hardlink_sandbox.blocker(_trigger()) == "executable_hardlink_forbidden"
    with pytest.raises(InvalidExecutableBindingError, match="executable_hardlink_forbidden"):
        hardlink_sandbox.argv((str(hardlink),), task_root, worktree)

    # When: an initially valid pinned executable changes after preflight
    hardlink.unlink()
    sandbox = _sandbox(source, hermes=broker)
    assert sandbox.blocker(_trigger()) is None
    broker.write_text("#!/bin/zsh\nprint -r -- substituted\n")
    broker.chmod(0o700)

    # Then: descriptor metadata/hash recheck rejects it before environment creation
    with pytest.raises(InvalidExecutableBindingError, match="executable_identity_changed"):
        sandbox.environment(_trigger(), experiment)


def test_registered_broker_cannot_spawn_child_shell(tmp_path: Path) -> None:
    # Given: a registered broker whose body attempts a forbidden child shell
    repository = Path(__file__).resolve().parents[1]
    hermes = repository / "tests" / "fixtures" / "dashboard" / "fake_hermes"
    broker = tmp_path / "malicious-broker"
    canary = tmp_path / "canary"
    canary.write_text("unchanged")
    broker.write_text(f"#!/bin/zsh\n/bin/sh -c 'echo changed > {canary}'\n")
    broker.chmod(0o700)
    task_root, experiment, worktree, source = _roots(tmp_path)
    sandbox = _sandbox(source, hermes=hermes, tools=(broker,))

    # When: the registered parent crosses the real sandbox boundary
    result = subprocess.run(
        sandbox.argv((str(broker),), task_root, worktree),
        cwd=worktree,
        env=sandbox.environment(_trigger(), experiment),
        check=False,
        capture_output=True,
    )

    # Then: process-exec for /bin/sh is denied and the canary is unchanged
    assert result.returncode != 0
    assert result.stdout == b""
    assert canary.read_text() == "unchanged"


def test_executor_rechecks_binding_after_preflight_before_process_launch(tmp_path: Path) -> None:
    # Given: the real executor completed preflight against one exact executable identity
    repository = Path(__file__).resolve().parents[1]
    hermes = tmp_path / "hermes"
    hermes.write_text("#!/bin/zsh\nprint -r -- original\n")
    hermes.chmod(0o700)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    executor = IsolatedWorktreeExecutor(
        repository=repository,
        environment_root=tmp_path / "environments",
        source_evidence_root=source,
        hermes_executable=hermes,
        fixture_mode=True,
    )
    payload = trigger_fixture(now=dt.datetime.now(dt.UTC))
    environment_spec = payload["environment_spec"]
    assert isinstance(environment_spec, dict)
    environment_spec["pinned_code_sha"] = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    trigger = AutonomousTriggerV1.model_validate(payload)
    assert executor.preflight(trigger) is None

    # When: the executable is substituted before execute
    hermes.write_text("#!/bin/zsh\nprint -r -- substituted\n")
    hermes.chmod(0o700)

    # Then: the executor's descriptor/hash recheck aborts before a model process launches
    with pytest.raises(InvalidExecutableBindingError, match="executable_identity_changed"):
        executor.execute(trigger, "substitution-probe")
    environments = tmp_path / "environments"
    assert not environments.exists() or not tuple(environments.iterdir())
