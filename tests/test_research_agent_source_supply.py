from __future__ import annotations

import csv
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.research_agent_primary_fixtures import NOW, seed_day, seed_opportunity, source_paths, write_service_config
from tests.research_agent_research_source_fixtures import populated_source_paths
from trading_agent.market_risk import MARKET_RISK_HEADER
from trading_agent.research_agent_configured_collector import ConfiguredResearchAgentEvidenceCollector
from trading_agent.research_agent_service_config import load_research_agent_service_config
from trading_agent.research_agent_source_supply import (
    InvalidMarketContextSupplyError,
    MarketContextSupplyUnavailableError,
    materialize_current_market_context,
)
from trading_agent.research_agent_source_supply_status import inspect_source_supply
from trading_agent.research_agent_systematic_input_models import BlockedSystematicInputActivation
from trading_agent.research_agent_systematic_input_store import write_systematic_input_activation

type RiskMutation = Literal["mode", "symlink", "hardlink", "header", "malformed", "bounds"]


def test_open_current_risk_screen_materializes_private_context_and_exact_replay(tmp_path: Path) -> None:
    paths = source_paths(tmp_path)
    seed_day(paths)

    first = materialize_current_market_context(paths, NOW)
    second = materialize_current_market_context(paths, NOW)

    artifacts = tuple(paths.market_context_root.glob("*.market-context.json"))
    assert first.created is True
    assert second.created is False
    assert first.snapshot == second.snapshot
    assert first.snapshot.valid_until - first.snapshot.observed_at == dt.timedelta(minutes=3)
    assert first.snapshot.order_authority is False
    assert first.snapshot.allocation_authority is False
    assert first.snapshot.lifecycle_authority is False
    assert len(artifacts) == 1
    assert artifacts[0].stat().st_mode & 0o777 == 0o600


def test_closed_session_is_typed_unavailable_and_creates_no_context(tmp_path: Path) -> None:
    paths = source_paths(tmp_path)
    seed_day(paths)

    with pytest.raises(MarketContextSupplyUnavailableError, match="session_closed"):
        materialize_current_market_context(paths, NOW.replace(hour=2))

    assert not paths.market_context_root.exists()


@pytest.mark.parametrize(
    "mutation",
    ("mode", "symlink", "hardlink", "header", "malformed", "bounds"),
)
def test_tampered_or_malformed_risk_screen_is_invalid(tmp_path: Path, mutation: RiskMutation) -> None:
    paths = source_paths(tmp_path)
    seed_day(paths)
    risk = paths.day_session_root / "20260803" / "market_risk_screen.csv"
    _mutate_risk(risk, mutation)

    with pytest.raises(InvalidMarketContextSupplyError):
        materialize_current_market_context(paths, NOW)

    assert not paths.market_context_root.exists()


@pytest.mark.parametrize(
    ("observed_at", "reason"),
    (
        (NOW - dt.timedelta(minutes=4), "current_risk_screen_stale"),
        (NOW - dt.timedelta(days=1), "current_risk_screen_prior_date"),
    ),
)
def test_stale_and_prior_date_risk_rows_never_create_current_context(
    tmp_path: Path,
    observed_at: dt.datetime,
    reason: str,
) -> None:
    paths = source_paths(tmp_path)
    seed_day(paths, observed_at=observed_at)
    current = paths.day_session_root / observed_at.astimezone(dt.timezone(dt.timedelta(hours=-4))).strftime("%Y%m%d")
    target = paths.day_session_root / "20260803"
    if current != target:
        target.mkdir(parents=True)
        (current / "market_risk_screen.csv").replace(target / "market_risk_screen.csv")

    with pytest.raises(MarketContextSupplyUnavailableError, match=reason):
        materialize_current_market_context(paths, NOW)

    assert not paths.market_context_root.exists()


def test_status_does_not_materialize_and_classifies_actionable_sources(tmp_path: Path) -> None:
    paths = source_paths(tmp_path)
    seed_day(paths)
    config_path = write_service_config(tmp_path, paths)
    config = load_research_agent_service_config(config_path)
    write_systematic_input_activation(
        config.systematic.input_activation,
        BlockedSystematicInputActivation(reason_code="minimum_clean_sessions_not_met", attempted_at=NOW),
    )

    report = inspect_source_supply(config, NOW, False)

    states = {item.agent_family_id: (item.state, item.reason) for item in report.families}
    assert not paths.market_context_root.exists()
    assert states["swing_trading"] == ("operator_action_required", "shadow_ledger_unavailable")
    assert states["systematic_quant"] == ("collecting", "minimum_clean_sessions_not_met")
    assert states["derivatives_research"] == (
        "operator_action_required",
        "external_realtime_entitlement_unverified",
    )
    assert tuple(item.agent_family_id for item in report.families) == (
        "opportunity_manager",
        "market_context",
        "day_trading",
        "swing_trading",
        "systematic_quant",
        "derivatives_research",
    )
    assert report.provider_calls == report.model_calls == report.network_calls == 0
    assert report.broker_mutation == report.order_authority_mutation == report.allocation_mutation == 0


def test_status_reports_tampered_risk_screen_without_materializing(tmp_path: Path) -> None:
    paths = source_paths(tmp_path)
    seed_day(paths)
    risk = paths.day_session_root / "20260803" / "market_risk_screen.csv"
    risk.chmod(0o644)
    config = load_research_agent_service_config(write_service_config(tmp_path, paths))

    report = inspect_source_supply(config, NOW, False)

    context = report.families[1]
    assert context.agent_family_id == "market_context"
    assert context.state == "blocked"
    assert context.reason == "risk_screen_private_file_invalid"
    assert not paths.market_context_root.exists()


def test_closed_status_maps_primary_sources_to_next_session_waiting(tmp_path: Path) -> None:
    paths = source_paths(tmp_path)
    config = load_research_agent_service_config(write_service_config(tmp_path, paths))

    report = inspect_source_supply(config, NOW.replace(hour=2), False)

    primary = report.families[:3]
    assert all(item.state == "waiting_session" and item.reason == "session_closed" for item in primary)
    assert all(item.next_action.startswith("wait_until_regular_session:") for item in primary)


def test_configured_collector_consumes_supply_and_isolates_tampering(tmp_path: Path) -> None:
    paths = source_paths(tmp_path)
    seed_day(paths)
    collector = ConfiguredResearchAgentEvidenceCollector(paths)

    supplied = collector.collect(NOW)
    risk = paths.day_session_root / "20260803" / "market_risk_screen.csv"
    risk.chmod(0o644)
    invalid = collector.collect(NOW)

    assert any(
        item.agent_family_id == "market_context" and ".blocked." not in item.source_key for item in supplied.evidence
    )
    assert all(item.agent_family_id != "market_context" for item in invalid.evidence)
    assert any(
        item.agent_family_id == "market_context"
        and item.reason == "market_context_supply.risk_screen_private_file_invalid"
        for item in invalid.failures
    )


def test_tick_cli_is_redacted_and_replay_reports_no_new_materialization(tmp_path: Path) -> None:
    paths = source_paths(tmp_path)
    seed_day(paths)
    seed_opportunity(paths)
    config = write_service_config(tmp_path, paths)
    command = (
        sys.executable,
        str(Path(__file__).parents[1] / "run_research_agent_source_supply.py"),
        "tick",
        "--config",
        str(config),
        "--now",
        NOW.isoformat(),
    )

    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first.returncode == second.returncode == 0
    assert first_payload["materialized_market_context"] is True
    assert second_payload["materialized_market_context"] is False
    assert str(tmp_path) not in first.stdout + second.stdout
    assert first_payload["provider_calls"] == first_payload["network_calls"] == 0
    assert first_payload["broker_mutation"] == first_payload["order_authority_mutation"] == 0


def test_derivatives_research_shadow_is_ready_without_current_quote_authority(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)
    for authority in (paths.outputs_root / "derivatives").glob("option_current_authority_*.json"):
        authority.unlink()
    config = load_research_agent_service_config(write_service_config(tmp_path, paths))

    report = inspect_source_supply(config, NOW, False)

    derivative = report.families[-1]
    assert derivative.agent_family_id == "derivatives_research"
    assert derivative.state == "ready"
    assert derivative.reason == "research_shadow_available_realtime_entitlement_missing"
    assert derivative.next_action == "continue_research_shadow_only"


def _mutate_risk(path: Path, mutation: RiskMutation) -> None:
    match mutation:
        case "mode":
            path.chmod(0o644)
        case "symlink":
            source = path.with_name("risk-source.csv")
            path.replace(source)
            path.symlink_to(source)
        case "hardlink":
            os.link(path, path.with_name("risk-hardlink.csv"))
        case "header":
            path.write_text("observed_at,symbol\n", encoding="utf-8")
        case "malformed":
            path.write_text(",".join(MARKET_RISK_HEADER) + "\nnot-a-time,NAS,AAPL\n", encoding="utf-8")
        case "bounds":
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(MARKET_RISK_HEADER)
                for index in range(5_001):
                    writer.writerow(_risk_row(f"A{index:04d}"))
        case unreachable:
            assert_never(unreachable)
    if mutation in {"header", "malformed", "bounds"}:
        path.chmod(0o600)


def _risk_row(symbol: str) -> tuple[str | float | int | bool, ...]:
    return (
        NOW.isoformat(),
        "NAS",
        symbol,
        True,
        "",
        0.08,
        10.0,
        9.99,
        10.01,
        18.0,
        58.0,
        2_000_000.0,
        300_000,
        1_000_000,
        0.3,
    )
