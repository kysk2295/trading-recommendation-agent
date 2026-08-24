from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import run_dashboard_publisher
from tests.dashboard_models_v2_fixtures import snapshot_payload
from trading_agent.dashboard_models import DashboardCredentials
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_system_current_authority import SystemAuthorityVerifierInput


def test_dashboard_publisher_cli_default_targets_schema_v2_runtime_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a local dry-run boundary that records the publisher's selected runtime config
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    credentials = DashboardCredentials(
        "https://example.test",
        SecretStr("fixture-value-with-adequate-length"),
    )
    snapshot = DashboardSnapshotV2.model_validate(snapshot_payload())
    observed_configs: list[Path] = []

    def fixed_credentials(_path: Path) -> DashboardCredentials:
        return credentials

    def observe_cycle_database(config_path: Path) -> None:
        observed_configs.append(config_path)

    def no_system_authority(_path: Path, *, untrusted_root: Path) -> None:
        return None

    def fixed_snapshot(
        _outputs: Path,
        *,
        system_authority_verifier: SystemAuthorityVerifierInput = None,
        cycle_database: Path | None = None,
        kr_day_state_root: Path | None = None,
    ) -> DashboardSnapshotV2:
        return snapshot

    monkeypatch.setattr(run_dashboard_publisher, "require_current_main_authority", lambda: None)
    monkeypatch.setattr(run_dashboard_publisher, "load_dashboard_credentials", fixed_credentials)
    monkeypatch.setattr(run_dashboard_publisher, "_cycle_database", observe_cycle_database)
    monkeypatch.setattr(run_dashboard_publisher, "load_system_authority_verifier", no_system_authority)
    monkeypatch.setattr(run_dashboard_publisher, "collect_dashboard_snapshot_v2", fixed_snapshot)

    # When: the real publish command runs without a research-agent-config override
    result = CliRunner().invoke(
        run_dashboard_publisher.app,
        ["publish", "--outputs", str(outputs), "--dry-run"],
    )

    # Then: the CLI succeeds after passing the canonical schema-v2 default to the loader boundary
    assert result.exit_code == 0
    assert observed_configs == [
        Path.home() / ".config" / "trading-agent" / "research-agent-runtime-v2.json",
    ]
