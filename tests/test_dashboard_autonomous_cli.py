from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

from dashboard_execution_support import run_autonomous_trigger
from typer.testing import CliRunner

import run_dashboard_publisher
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1, trigger_fixture
from trading_agent.dashboard_trigger_authority import (
    TriggerAuthorityStore,
    authority_record_for,
)


def test_test_only_runner_happy_duplicate_and_production_cli_missing_authority(tmp_path: Path) -> None:
    # Given: one typed trigger and its exact persisted source authority
    _, trigger = _trigger_path(tmp_path)
    state_root = tmp_path / "state"
    assert TriggerAuthorityStore(state_root / "authorities").append(authority_record_for(trigger))

    # When: the explicit test runner handles success/replay and production CLI handles no authority
    first = run_autonomous_trigger(trigger, state_root=state_root, receipts=[])
    duplicate = run_autonomous_trigger(trigger, state_root=state_root, receipts=[])
    runner = CliRunner()
    missing_path, _ = _trigger_path(tmp_path / "missing")
    missing = runner.invoke(
        run_dashboard_publisher.app,
        [
            "autonomous-agent",
            "--trigger-fixture",
            str(missing_path),
            "--state-root",
            str(tmp_path / "missing-state"),
        ],
    )

    # Then: one model process completes and replay/missing authority launch zero
    assert first.state == "completed"
    assert first.model_processes == 1
    assert duplicate.model_processes == 0
    assert missing.exit_code == 0
    assert "AUTONOMOUS_BLOCKED model_processes=0 receipt=1" in missing.stdout
    help_result = runner.invoke(run_dashboard_publisher.app, ["autonomous-agent", "--help"])
    assert "--fake-hermes" not in help_result.stdout
    assert "--hermes-executable" not in help_result.stdout


def test_autonomous_cli_invalid_trigger_appends_typed_rejection(tmp_path: Path) -> None:
    # Given: a mode-600 malformed trigger fixture
    trigger_path = tmp_path / "invalid.autonomous-trigger.json"
    trigger_path.write_text('{"schema_version":1,"private_session":"forbidden"}')
    trigger_path.chmod(0o600)
    state_root = tmp_path / "state"

    # When: the real CLI parser rejects it
    result = CliRunner().invoke(
        run_dashboard_publisher.app,
        [
            "autonomous-agent",
            "--trigger-fixture",
            str(trigger_path),
            "--state-root",
            str(state_root),
        ],
    )

    # Then: parsing exits before process launch and one typed rejection is durable
    assert result.exit_code == 2
    receipts = tuple((state_root / "rejected").glob("*.json"))
    assert len(receipts) == 1
    assert '"state":"rejected"' in receipts[0].read_text()


def _trigger_path(root: Path) -> tuple[Path, AutonomousTriggerV1]:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = dt.datetime.now(dt.UTC)
    payload = trigger_fixture(now=now)
    environment = payload["environment_spec"]
    assert isinstance(environment, dict)
    repository = Path(__file__).resolve().parents[1]
    environment["pinned_code_sha"] = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    trigger = AutonomousTriggerV1.model_validate(payload)
    path = root / "trigger.autonomous-trigger.json"
    path.write_text(trigger.model_dump_json())
    path.chmod(0o600)
    return path, trigger
