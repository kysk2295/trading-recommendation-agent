from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pytest

import trading_agent.research_agent_service_builder as service_builder
from run_research_agent_runtime import main
from tests.research_agent_browser_service_fixtures import browser_service_config
from tests.research_agent_service_kr_v4_support import (
    durable_tool_request_result_counts,
    install_memory_search_instrumentation,
    tool_proposal_client,
)
from tests.test_browser_research_agenda import NOW as AGENDA_NOW
from tests.test_browser_research_agenda import agenda_services_fixture
from tests.test_kr_autonomous_trade_planner import _request
from tests.test_kr_virtual_position_store import _other_task_event, _recommendation_for_task
from trading_agent.autonomous_supervisor_service import autonomous_supervisor_paths
from trading_agent.autonomous_supervisor_status import (
    KrAutonomousSupervisorStatus,
    autonomous_supervisor_status_for_config,
)
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.browser_research_agenda import ContinuousBrowserResearchSupervisor
from trading_agent.kr_autonomous_trade_models import KrOpenVirtualExposure, KrTradeRecommendation
from trading_agent.kr_autonomous_trade_planner import plan_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_virtual_position_engine import advance_kr_virtual_position, arm_kr_virtual_position
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore
from trading_agent.research_agent_service_cli_args import config_from_provision_args
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_service_runtime import build_service_runtime

NOW = dt.datetime(2026, 8, 27, 3, 0, tzinfo=dt.UTC)


def test_v2_agenda_migrates_one_v1_episode_with_lineage_and_open_ownership(tmp_path: Path) -> None:
    # Given: an existing v1 agenda task and its original durable evidence.
    services = agenda_services_fixture(tmp_path)
    predecessor = services.ensure_open(AGENDA_NOW)
    original = services.cycles.evidence(predecessor.root_source_evidence_id)
    assert original is not None
    original_payload = original.evidence.bounded_payload_json
    migrated = ContinuousBrowserResearchSupervisor(
        services.supervisor, services.cycles, owns_cycles=False, agenda_version=2
    )

    # When: schema-v4 startup ensures the KR decision agenda twice.
    successor = migrated.ensure_open(AGENDA_NOW + dt.timedelta(seconds=1))
    replay = migrated.ensure_open(AGENDA_NOW + dt.timedelta(seconds=2))

    # Then: the v1 payload remains exact and one open-ended v2 successor retains lineage.
    assert replay.task_id == successor.task_id != predecessor.task_id
    assert original.evidence.bounded_payload_json == original_payload
    assert len(migrated.episodes.all()) == 2
    episode = migrated.episodes.get_by_task(successor.task_id)
    assert episode is not None and episode.agenda_version == 2
    assert episode.predecessor_task_id == predecessor.task_id
    root = migrated.cycles.evidence(successor.root_source_evidence_id)
    assert root is not None and predecessor.task_id in root.evidence.evidence_refs
    assert successor.agent_version == "browser-research-agenda-v2"
    plan = "\n".join(successor.current_plan)
    assert not any(name in plan for name in ("social.signal.normalize", "kr.market.corroborate", "kr.trade.plan"))


def test_provision_selects_schema_v4_from_explicit_kr_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the provision surface and a schema-v3 source configuration.
    assert main(("provision", "--help")) == 0
    help_text = capsys.readouterr().out
    source = browser_service_config(tmp_path)
    args = _provision_args(source)
    args.kr_market_receipt_root = tmp_path / "market-receipts"
    args.kr_social_signal_database = tmp_path / "social-signals.sqlite3"

    # When: the CLI arguments are parsed into a service candidate.
    candidate = config_from_provision_args(args)

    # Then: exact browser and KR bindings opt into schema v4.
    assert "--kr-market-receipt-root" in help_text and "--kr-social-signal-database" in help_text
    assert candidate.schema_version == 4
    assert candidate.browser_gateway_config == source.browser_gateway_config
    assert candidate.kr_market_receipt_root == args.kr_market_receipt_root.absolute()
    assert candidate.kr_social_signal_database == args.kr_social_signal_database.absolute()


def test_v4_status_reports_kr_decision_and_virtual_position_counts(tmp_path: Path) -> None:
    # Given: one signal, one recommendation, one no-trade, one open and one terminal virtual position.
    config = _v4_config(tmp_path)
    request = _request()
    recommendation = plan_kr_autonomous_trade(request)
    assert isinstance(recommendation, KrTradeRecommendation)
    duplicate = plan_kr_autonomous_trade(
        request.model_copy(
            update={
                "previous_event_id": recommendation.event_id,
                "open_exposures": (KrOpenVirtualExposure(symbol=request.thesis.symbol, theme=request.thesis.theme),),
            }
        )
    )
    kr_root = config.output_root / "autonomous-supervisor" / "kr-v1"
    assert config.kr_social_signal_database is not None
    assert KrSocialSignalStore(config.kr_social_signal_database).append(request.social_signal)
    trades = KrAutonomousTradeStore(kr_root / "kr-autonomous-trades.sqlite3")
    assert trades.append(recommendation)
    assert trades.append(duplicate)
    positions = KrVirtualPositionStore(kr_root / "kr-virtual-positions.sqlite3")
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    assert positions.append(armed)
    assert positions.append(_other_task_event(armed))
    terminal = advance_kr_virtual_position(recommendation, armed, (), recommendation.valid_until)
    assert len(terminal) == 1 and positions.append(terminal[0])

    # When: the production status projection reads the private v4 stores.
    status = autonomous_supervisor_status_for_config(config, NOW)

    # Then: every bounded KR artifact is counted and all mutation authority remains zero.
    assert isinstance(status, KrAutonomousSupervisorStatus)
    assert status.social_signals == 1
    assert status.recommendations == 1
    assert status.no_trade_decisions == 1
    assert status.open_virtual_positions == 1
    assert status.terminal_virtual_positions == 1
    assert status.broker_mutation == status.trading_mutation == 0


def test_v4_service_vertical_migrates_reconciles_and_restarts_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: production stores with one v1 episode and one open Task5 virtual position.
    source = browser_service_config(tmp_path)
    proposal_client = tool_proposal_client(tmp_path, AGENDA_NOW + dt.timedelta(minutes=5))
    build_supervisor = service_builder.build_autonomous_supervisor
    install_memory_search_instrumentation(monkeypatch)
    monkeypatch.setattr(
        service_builder,
        "build_autonomous_supervisor",
        lambda candidate, browser=None: build_supervisor(candidate, client=proposal_client, browser=browser),
    )
    legacy = build_service_runtime(source)
    _ = legacy.tick(AGENDA_NOW)
    legacy.close()
    config = ResearchAgentServiceConfig.model_validate(
        source.model_dump(mode="python")
        | {
            "schema_version": 4,
            "kr_market_receipt_root": tmp_path / "market-receipts",
            "kr_social_signal_database": tmp_path / "social-signals.sqlite3",
        }
    )
    paths = autonomous_supervisor_paths(config)
    tasks = AutonomousTaskStore(paths.task_database)
    predecessor = next(task for task in tasks.reader().tasks() if task.agent_version == "browser-research-agenda-v1")
    tasks.close()
    recommendation = _recommendation_for_task(predecessor.task_id)
    kr_root = paths.task_database.parent / "kr-v1"
    assert KrAutonomousTradeStore(kr_root / "kr-autonomous-trades.sqlite3").append(recommendation)
    positions = KrVirtualPositionStore(kr_root / "kr-virtual-positions.sqlite3")
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    assert positions.append(armed)

    # When: v4 starts, ticks once, and restarts at the exact same logical time.
    runtime = build_service_runtime(config)
    reconciled_before_tick = positions.events(armed.position_id)
    _ = runtime.tick(AGENDA_NOW + dt.timedelta(seconds=1))
    episode_count = sum(
        evidence.source_key == "browser_research_agenda.episode" for evidence in runtime.store.all_evidence()
    )
    runtime.close()
    tasks = AutonomousTaskStore(paths.task_database)
    successor = next(task for task in tasks.reader().tasks() if task.agent_version == "browser-research-agenda-v2")
    tasks.close()
    durable_before_restart = durable_tool_request_result_counts(paths.task_database, successor.task_id)
    invocation_marker = paths.memory_database.with_suffix(".tool-invocations")
    invocations_before_restart = invocation_marker.read_text(encoding="ascii").splitlines().count(successor.task_id)
    restarted = build_service_runtime(config)
    event_count_before = len(positions.events(armed.position_id))
    _ = restarted.tick(AGENDA_NOW + dt.timedelta(seconds=1))
    restarted.close()
    durable_after_restart = durable_tool_request_result_counts(paths.task_database, successor.task_id)
    invocations_after_restart = invocation_marker.read_text(encoding="ascii").splitlines().count(successor.task_id)

    # Then: reconciliation precedes work, one v2 successor exists, and replay adds no duplicate event.
    assert tuple(event.state.value for event in reconciled_before_tick) == ("ARMED", "EXPIRED")
    assert episode_count == 2
    assert len(positions.events(armed.position_id)) == event_count_before
    assert durable_before_restart == durable_after_restart == (1, 1, 0)
    assert invocations_before_restart == invocations_after_restart == 1
    status = autonomous_supervisor_status_for_config(config, NOW)
    assert isinstance(status, KrAutonomousSupervisorStatus)
    assert status.broker_mutation == status.trading_mutation == 0


def _v4_config(tmp_path: Path) -> ResearchAgentServiceConfig:
    source = browser_service_config(tmp_path)
    return ResearchAgentServiceConfig.model_validate(
        source.model_dump(mode="python")
        | {
            "schema_version": 4,
            "kr_market_receipt_root": tmp_path / "market-receipts",
            "kr_social_signal_database": tmp_path / "social-signals.sqlite3",
        }
    )


def _provision_args(config: ResearchAgentServiceConfig) -> argparse.Namespace:
    sources, systematic = config.source_paths, config.systematic
    return argparse.Namespace(
        project_root=config.project_root,
        uv_path=config.uv_path,
        hermes_executable=config.hermes_executable,
        python_executable=systematic.python_executable,
        cycle_database=config.cycle_database,
        output_root=config.output_root,
        hermes_database=config.hermes_database,
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
        model_id=config.model_id,
        provider_id=config.provider_id,
        max_runtime_seconds=systematic.max_runtime_seconds,
        max_bars=systematic.max_bars,
        max_sessions=systematic.max_sessions,
        rss_limit_gib=systematic.rss_limit_gib,
        browser_gateway_config=config.browser_gateway_config,
    )
