from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from run_research_agent_runtime import main
from tests.research_agent_browser_service_fixtures import browser_gateway_config
from tests.test_research_agent_service_cli import _config, _provision
from trading_agent.research_agent_service_cli_args import config_from_provision_args
from trading_agent.research_agent_service_config import (
    InvalidResearchAgentServiceConfigError,
    ResearchAgentServiceConfig,
    write_research_agent_launch_agent,
    write_research_agent_service_config,
)
from trading_agent.research_agent_service_runtime import service_status


def test_provision_help_exposes_optional_browser_gateway(capsys: pytest.CaptureFixture[str]) -> None:
    # Given/When: the existing provision command help is requested.
    assert main(("provision", "--help")) == 0

    # Then: schema v3 is opt-in through one optional path argument.
    assert "--browser-gateway-config" in capsys.readouterr().out


def test_schema_v2_config_remains_canonical_without_browser_field(tmp_path: Path) -> None:
    # Given: the shipped schema-v2 service configuration.
    config = _config(tmp_path)
    path = tmp_path / "private" / "service-v2.json"

    # When: it is written through the canonical config boundary.
    assert write_research_agent_service_config(path, config)
    payload = json.loads(path.read_text(encoding="utf-8"))

    # Then: the old field set and nested null remain byte-for-byte canonical.
    assert payload["schema_version"] == 2
    assert "browser_gateway_config" not in payload
    assert payload["source_paths"]["kr_calendar_store"] is None
    assert path.read_text(encoding="utf-8") == (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    )
    report = service_status(config, dt.datetime(2026, 8, 27, tzinfo=dt.UTC))
    assert report.schema_version == 2
    assert report.config_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("schema_version", "gateway_path"),
    ((2, Path("/tmp/gateway.json")), (3, None), (3, Path("gateway.json"))),
)
def test_schema_and_browser_gateway_path_must_form_a_valid_pair(
    tmp_path: Path,
    schema_version: int,
    gateway_path: Path | None,
) -> None:
    # Given: a schema/path pair outside the backward-compatible contract.
    payload = _config(tmp_path).model_dump(mode="python")
    payload.update(schema_version=schema_version, browser_gateway_config=gateway_path)

    # When/Then: the typed config boundary rejects it.
    with pytest.raises(InvalidResearchAgentServiceConfigError):
        ResearchAgentServiceConfig.model_validate(payload)


def test_provision_config_selects_schema_from_optional_gateway_path(tmp_path: Path) -> None:
    # Given: provision arguments equivalent to an existing v2 configuration.
    source = _config(tmp_path)
    values = source.model_dump(mode="python")
    systematic = source.systematic
    sources = source.source_paths
    args = argparse.Namespace(
        project_root=source.project_root,
        uv_path=source.uv_path,
        hermes_executable=source.hermes_executable,
        python_executable=systematic.python_executable,
        cycle_database=source.cycle_database,
        output_root=source.output_root,
        hermes_database=source.hermes_database,
        source_outputs_root=sources.outputs_root,
        source_market_context_root=sources.market_context_root,
        source_day_session_root=sources.day_session_root,
        source_swing_shadow_database=sources.swing_shadow_database,
        source_swing_review_database=sources.swing_review_database,
        source_experiment_ledger=sources.experiment_ledger,
        source_lane_review_database=sources.lane_review_database,
        source_kr_calendar_store=sources.kr_calendar_store,
        systematic_context=systematic.context,
        systematic_response_fixture=systematic.response_fixture,
        systematic_experiment_ledger=systematic.experiment_ledger,
        systematic_receipt_root=systematic.receipt_root,
        systematic_strategy_root=systematic.strategy_root,
        systematic_manifest_root=systematic.manifest_root,
        systematic_queue_root=systematic.queue_root,
        systematic_input_activation=systematic.input_activation,
        systematic_artifact_root=systematic.artifact_root,
        systematic_review_root=systematic.review_root,
        systematic_runs_root=systematic.runs_root,
        model_id=values["model_id"],
        provider_id=values["provider_id"],
        max_runtime_seconds=systematic.max_runtime_seconds,
        max_bars=systematic.max_bars,
        max_sessions=systematic.max_sessions,
        rss_limit_gib=systematic.rss_limit_gib,
        browser_gateway_config=None,
    )

    # When: the optional gateway argument is absent and then present.
    v2 = config_from_provision_args(args)
    _, gateway_path = browser_gateway_config(tmp_path / "browser")
    args.browser_gateway_config = gateway_path
    v3 = config_from_provision_args(args)

    # Then: absence retains v2 while presence opts into v3 with an absolute path.
    assert (v2.schema_version, v2.browser_gateway_config) == (2, None)
    assert (v3.schema_version, v3.browser_gateway_config) == (3, gateway_path.absolute())


def test_v3_activate_rejects_bad_gateway_config_before_launchctl(tmp_path: Path) -> None:
    # Given: a valid service contract whose referenced gateway config becomes non-private.
    service = _config(tmp_path)
    _, gateway_path = browser_gateway_config(tmp_path / "browser")
    v3 = ResearchAgentServiceConfig.model_validate(
        service.model_dump(mode="python") | {"schema_version": 3, "browser_gateway_config": gateway_path}
    )
    config_path, plist_path = _provision(tmp_path / "service")
    config_path.unlink()
    plist_path.unlink()
    assert write_research_agent_service_config(config_path, v3)
    assert write_research_agent_launch_agent(plist_path, v3, config_path)
    gateway_path.chmod(0o644)
    calls: list[tuple[str, ...]] = []

    # When: activation verifies the service before launchctl.
    code = main(
        ("activate", "--config", str(config_path), "--plist", str(plist_path)),
        runner=lambda command: calls.append(command) or 0,
    )

    # Then: gateway verification fails closed without starting either service.
    assert code == 2
    assert calls == []
