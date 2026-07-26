from __future__ import annotations

import datetime as dt
import inspect
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from dashboard_execution_support import (
    FixtureScenario,
    execution_sandbox,
    fixture_identity,
    worktree_executor,
)

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1, trigger_fixture
from trading_agent.dashboard_executable_binding import (
    InvalidExecutableBindingError,
    capture_file,
    capture_python_entrypoint,
)
from trading_agent.dashboard_execution_identity import BoundExecutionIdentity
from trading_agent.dashboard_execution_sandbox import (
    _ExecutionSandbox,
    create_production_execution_sandbox,
)


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
    repository: Path,
    source: Path,
    identity: BoundExecutionIdentity,
) -> _ExecutionSandbox:
    return execution_sandbox(repository, source, identity)


def _run(
    sandbox: _ExecutionSandbox,
    identity: BoundExecutionIdentity,
    trigger: AutonomousTriggerV1,
    task_root: Path,
    experiment: Path,
    worktree: Path,
    *,
    prompt: str = "",
    extra_environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    environment = sandbox.environment(trigger, experiment)
    if extra_environment is not None:
        environment.update(extra_environment)
    return subprocess.run(
        sandbox.argv(identity.request(prompt), task_root, worktree),
        cwd=worktree,
        env=environment,
        check=False,
        capture_output=True,
    )


def test_fixed_fixture_model_real_hermes_probe_and_native_broker_execute(tmp_path: Path) -> None:
    # Given: three code-owned identities with immutable argv templates
    repository = Path(__file__).resolve().parents[1]
    task_root, experiment, worktree, source = _roots(tmp_path)
    trigger = _trigger()
    fixture = fixture_identity(repository)
    probe_sandbox = create_production_execution_sandbox(
        repository=repository,
        source_evidence_root=source,
        execution_id="hermes-probe",
    )
    probe = probe_sandbox.execution_identity
    broker_sandbox = create_production_execution_sandbox(
        repository=repository,
        source_evidence_root=source,
        execution_id="health-broker",
    )
    broker = broker_sandbox.execution_identity

    # When: each identity crosses its separate real sandbox boundary
    fixture_result = _run(
        _sandbox(repository, source, fixture),
        fixture,
        trigger,
        task_root,
        experiment,
        worktree,
        prompt="fixture",
    )
    probe_result = _run(
        probe_sandbox,
        probe,
        trigger,
        task_root,
        experiment,
        worktree,
    )
    broker_result = _run(
        broker_sandbox,
        broker,
        trigger,
        task_root,
        experiment,
        worktree,
    )

    # Then: the fixed model, resolved real Hermes entrypoint, and native broker succeed
    assert fixture_result.returncode == 0
    assert fixture_result.stdout == b"candidate evidence recorded\n"
    assert (experiment / "candidate.json").read_text() == '{"candidate": "verified"}\n'
    assert probe_result.returncode == 0
    assert b"Hermes Agent" in probe_result.stdout
    assert broker_result.returncode == 0
    assert broker_result.stdout == b""


@pytest.mark.parametrize(
    "mutation",
    ["python-c", "python-m", "script", "appended", "digest", "role"],
)
def test_argv_mutation_never_matches_bound_template(tmp_path: Path, mutation: str) -> None:
    # Given: one valid code-owned fixture request
    repository = Path(__file__).resolve().parents[1]
    task_root, _, worktree, source = _roots(tmp_path)
    identity = fixture_identity(repository)
    sandbox = _sandbox(repository, source, identity)
    request = identity.request("fixture")
    mutations = {
        "python-c": replace(request, argv=(request.argv[0], "-c", "print('escape')")),
        "python-m": replace(request, argv=(request.argv[0], "-m", "http.server")),
        "script": replace(request, argv=(request.argv[0], "/tmp/substitute.py")),
        "appended": replace(request, argv=(*request.argv, "--extra")),
        "digest": replace(request, template_digest="0" * 64),
        "role": replace(request, role="health-broker"),
    }

    # When/Then: direct Python roles and every altered field fail before sandbox launch
    with pytest.raises(InvalidExecutableBindingError, match="execution_request_not_bound"):
        sandbox.argv(mutations[mutation], task_root, worktree)


@pytest.mark.parametrize("interpreter", ["/bin/sh", "/bin/bash", "/bin/zsh", "/usr/bin/env"])
def test_shell_and_env_interpreters_are_unconditionally_rejected(
    tmp_path: Path,
    interpreter: str,
) -> None:
    # Given: a safe-mode script whose interpreter is a shell or env
    script = tmp_path / "candidate"
    script.write_text(f"#!{interpreter}\nexit 0\n")
    script.chmod(0o700)

    # When/Then: identity construction rejects it regardless of hash and mode
    with pytest.raises(InvalidExecutableBindingError, match="shell_interpreter_forbidden"):
        capture_python_entrypoint(script)


def test_production_factory_rejects_identity_and_catalog_injection(tmp_path: Path) -> None:
    # Given: caller-captured binaries and a code-owned test identity
    repository = Path(__file__).resolve().parents[1]
    _, _, _, source = _roots(tmp_path)
    arbitrary = fixture_identity(repository)

    # When/Then: the production signature has no identity, catalog, or fixture selector
    with pytest.raises(TypeError):
        inspect.signature(create_production_execution_sandbox).bind(
            repository=repository,
            source_evidence_root=source,
            execution_id="health-broker",
            execution_identity=arbitrary,
        )


@pytest.mark.parametrize(
    ("scenario", "extra_environment"),
    [
        ("exec-escape", None),
        ("filesystem-escape", {"DASHBOARD_ESCAPE_CANARY": "{canary}"}),
        ("network-escape", None),
    ],
)
def test_guard_and_sandbox_confine_generated_python(
    tmp_path: Path,
    scenario: str,
    extra_environment: dict[str, str] | None,
) -> None:
    # Given: fixed test code attempting same-Python exec, host write, or network
    repository = Path(__file__).resolve().parents[1]
    task_root, experiment, worktree, source = _roots(tmp_path)
    canary = tmp_path / "canary"
    canary.write_text("unchanged")
    identity = fixture_identity(repository, cast(FixtureScenario, scenario))
    sandbox = _sandbox(repository, source, identity)
    environment = (
        None
        if extra_environment is None
        else {key: value.format(canary=canary) for key, value in extra_environment.items()}
    )

    # When: the code crosses the guarded Python and OS sandbox boundary
    result = _run(
        sandbox,
        identity,
        _trigger(),
        task_root,
        experiment,
        worktree,
        prompt="escape",
        extra_environment=environment,
    )

    # Then: it exits nonzero without stdout, host mutation, or surviving environment
    assert result.returncode != 0
    assert result.stdout == b""
    assert canary.read_text() == "unchanged"


def test_symlink_hardlink_path_traversal_and_toctou_fail_before_launch(tmp_path: Path) -> None:
    # Given: link aliases, traversal, and a catalog identity altered after capture
    repository = Path(__file__).resolve().parents[1]
    target = tmp_path / "target"
    target.write_text("fixed")
    target.chmod(0o700)
    identity = fixture_identity(repository)
    altered = replace(identity, target=capture_file(target, executable=True))
    symlink = tmp_path / "symlink"
    symlink.symlink_to(target)
    hardlink = tmp_path / "hardlink"
    os.link(target, hardlink)
    traversal = tmp_path / "nested" / ".." / "target"

    # When/Then: every alias and caller substitution is rejected deterministically
    with pytest.raises(InvalidExecutableBindingError, match="executable_symlink_forbidden"):
        capture_file(symlink, executable=True)
    with pytest.raises(InvalidExecutableBindingError, match="executable_hardlink_forbidden"):
        capture_file(hardlink, executable=True)
    with pytest.raises(InvalidExecutableBindingError, match="executable_path_traversal"):
        capture_file(traversal, executable=True)
    assert altered is not identity


def test_executor_rechecks_fixed_identity_after_preflight(tmp_path: Path) -> None:
    # Given: an executor whose fixed identity passes preflight
    repository = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    executor = worktree_executor(
        repository=repository,
        environment_root=tmp_path / "environments",
        source_evidence_root=source,
    )
    identity = executor._execution_identity
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

    # When: caller substitution creates an unregistered identity after preflight
    executor.__dict__["_execution_identity"] = replace(identity, identity_digest="0" * 64)

    # Then: the sandbox retains its original binding and rejects the substituted request
    with pytest.raises(InvalidExecutableBindingError, match="execution_request_not_bound"):
        executor.execute(trigger, "substitution-probe")
