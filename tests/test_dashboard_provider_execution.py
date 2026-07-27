from __future__ import annotations

import datetime as dt
import subprocess
from dataclasses import replace
from pathlib import Path

from dashboard_execution_support import execution_sandbox

from trading_agent.dashboard_autonomous_research import (
    AutonomousTriggerV1,
    trigger_fixture,
)
from trading_agent.dashboard_execution_catalog import (
    ProductionExecutionId,
    _build_expected_execution,
)


def _trigger() -> AutonomousTriggerV1:
    return AutonomousTriggerV1.model_validate(
        trigger_fixture(now=dt.datetime.now(dt.UTC))
    )


def _roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    task_root = tmp_path / "task"
    experiment = task_root / "experiment"
    worktree = task_root / "worktree"
    source = tmp_path / "source"
    for path in (experiment, worktree, source):
        path.mkdir(mode=0o700, parents=True)
    return task_root, experiment, worktree, source


def test_production_model_environment_materializes_isolated_codex_credentials(tmp_path: Path) -> None:
    # Given: the production Hermes model identity and one private host auth store
    repository = Path(__file__).resolve().parents[1]
    _, experiment, _, source = _roots(tmp_path)
    auth_store = tmp_path / "auth.json"
    auth_store.write_text('{"provider":"fixture"}')
    auth_store.chmod(0o600)
    expected = _build_expected_execution(repository, ProductionExecutionId.HERMES_MODEL)
    identity = replace(
        expected,
        readable_literals=(expected.readable_literals[0], auth_store),
    )
    sandbox = execution_sandbox(repository, source, identity)

    # When: the boundary creates the isolated model environment
    model_environment = sandbox.environment(_trigger(), experiment)
    repeated_environment = sandbox.environment(_trigger(), experiment)

    # Then: repeated boundary derivation is safe and reuses the task-local mode-600 copy
    assert repeated_environment == model_environment
    assert model_environment["HERMES_INFERENCE_PROVIDER"] == "openai-codex"
    assert model_environment["HERMES_INFERENCE_MODEL"] == "gpt-5.5"
    isolated_auth = Path(model_environment["HERMES_HOME"]) / "auth.json"
    assert isolated_auth.read_text() == auth_store.read_text()
    assert isolated_auth.stat().st_mode & 0o777 == 0o600


def test_provider_proxy_sandbox_profile_accepts_loopback_endpoint(tmp_path: Path) -> None:
    # Given: a real Hermes probe request with the production provider-proxy profile shape
    repository = Path(__file__).resolve().parents[1]
    task_root, experiment, worktree, source = _roots(tmp_path)
    identity = _build_expected_execution(repository, ProductionExecutionId.HERMES_PROBE)
    sandbox = execution_sandbox(repository, source, identity)

    # When: macOS sandbox-exec parses and runs the loopback-constrained profile
    result = subprocess.run(
        sandbox.argv(identity.request(), task_root, worktree, provider_proxy_port=9443),
        cwd=worktree,
        env=sandbox.environment(_trigger(), experiment),
        check=False,
        capture_output=True,
    )

    # Then: the network rule is valid without granting a remote provider endpoint
    assert result.returncode == 0
    assert result.stdout == b"Hermes Agent entrypoint verified\n"
