from __future__ import annotations

import datetime as dt
import subprocess
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


def test_production_model_environment_pins_openrouter_provider_and_model(tmp_path: Path) -> None:
    # Given: the production Hermes model boundary with an available OpenRouter credential source
    repository = Path(__file__).resolve().parents[1]
    _, experiment, _, source = _roots(tmp_path)
    identity = _build_expected_execution(repository, ProductionExecutionId.HERMES_MODEL)
    sandbox = execution_sandbox(repository, source, identity)

    # When: the boundary creates the isolated model environment
    model_environment = sandbox.environment(_trigger(), experiment)

    # Then: provider routing is explicit instead of falling back to Codex session credentials
    assert model_environment["HERMES_INFERENCE_PROVIDER"] == "openrouter"
    assert model_environment["HERMES_INFERENCE_MODEL"] == "openai/gpt-5.4-mini"
    assert Path(model_environment["HERMES_HOME"]) == identity.readable_literals[0].resolve().parent


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
