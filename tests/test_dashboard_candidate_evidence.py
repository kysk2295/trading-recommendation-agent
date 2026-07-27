from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

from dashboard_execution_support import worktree_executor

from trading_agent.dashboard_autonomous_research import (
    AutonomousTriggerV1,
    trigger_fixture,
)


def test_executor_persists_stdout_only_model_result_as_candidate_evidence(tmp_path: Path) -> None:
    # Given: a bound model that returns a candidate without writing an experiment file
    repository = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    executor = worktree_executor(
        repository=repository,
        environment_root=tmp_path / "environments",
        source_evidence_root=source,
        scenario="model-stdout-only",
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

    # When: the isolated loop executes the model and downstream research broker
    result = executor.execute(trigger, "stdout-candidate-probe")

    # Then: model output becomes a fourth append-only experiment evidence artifact
    assert result.state == "completed"
    assert len(result.evidence_sha256) == 4
