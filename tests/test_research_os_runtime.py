from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Never

import anyio
import pytest

import run_research_agent_runtime
from tests.strategy_research_contract_fixtures import NOW as RESEARCH_NOW
from tests.test_research_agent_service_cli import _config
from tests.test_strategy_research_runtime import _work
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_service_config import (
    ResearchAgentServiceConfig,
    write_research_agent_service_config,
)
from trading_agent.research_os_runtime import (
    run_research_os_forever,
    run_research_os_tick,
    strategy_research_work_root,
)
from trading_agent.strategy_lab_research_evidence import StrategyLabResultSourceAdapter
from trading_agent.strategy_research_runtime_source import StrategyResearchWorkQueue
from trading_agent.strategy_research_types import ResearchAgentId

UTC = dt.UTC
POST_CLOSE = dt.datetime(2026, 8, 17, 21, 0, tzinfo=UTC)


def _no_action_hermes(path: Path) -> Path:
    payload = json.dumps(
        {
            "schema_version": 1,
            "primary_decision": "no_action",
            "question": "Is any bounded role action required now?",
            "summary": "No bounded role action is required for this healthy fixture.",
            "reason": "fixture_idle",
            "continuation": "Wait for independently new evidence before another role action.",
            "open_work_ref": None,
            "requested_action": None,
            "subject_refs": [],
            "next_wake_kind": "new_evidence",
            "next_wake_at": None,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{payload}'\n", encoding="utf-8")
    os.chmod(path, 0o700)
    return path


def test_run_command_dispatches_the_combined_persistent_research_os(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid persistent-runtime config and observable legacy/combined runners.
    config = _config(tmp_path)
    config_path = (tmp_path / "private" / "runtime.json").absolute()
    assert write_research_agent_service_config(config_path, config)
    legacy_calls: list[ResearchAgentServiceConfig] = []
    combined_calls: list[ResearchAgentServiceConfig] = []

    async def legacy(candidate: ResearchAgentServiceConfig) -> None:
        legacy_calls.append(candidate)

    async def combined(candidate: ResearchAgentServiceConfig) -> None:
        combined_calls.append(candidate)

    monkeypatch.setattr(run_research_agent_runtime, "run_service_forever", legacy, raising=False)
    monkeypatch.setattr(
        run_research_agent_runtime,
        "run_research_os_forever",
        combined,
        raising=False,
    )

    # When: launchd's existing `run` command is dispatched.
    code = run_research_agent_runtime.main(("run", "--config", str(config_path)))

    # Then: the combined OS loop owns the process and the role-only loop is not started.
    assert code == 0
    assert combined_calls == [config]
    assert legacy_calls == []


def test_combined_tick_runs_one_independent_science_cycle_and_persists_restart_state(
    tmp_path: Path,
) -> None:
    # Given: one due momentum work item and no work for the other five families.
    config = _config(tmp_path)
    root = strategy_research_work_root(config)
    root.mkdir(parents=True)
    os.chmod(root, 0o700)
    work = _work(ResearchAgentId.INTRADAY_MOMENTUM, RESEARCH_NOW + dt.timedelta(minutes=2))
    queue_path = root / f"{ResearchAgentId.INTRADAY_MOMENTUM.value}.json"
    queue_path.write_text(StrategyResearchWorkQueue(items=(work,)).model_dump_json(), encoding="utf-8")
    os.chmod(queue_path, 0o600)

    # When: one OS tick completes the work and a fresh tick process replays the persisted state.
    first = run_research_os_tick(config, RESEARCH_NOW + dt.timedelta(minutes=2))
    restarted = run_research_os_tick(config, RESEARCH_NOW + dt.timedelta(minutes=2, seconds=30))

    # Then: production starts one heavy cycle, five missing families do not block it, and replay is idle.
    assert first.strategy_research.heavy_agent_id is ResearchAgentId.INTRADAY_MOMENTUM
    assert first.strategy_research.heavy_cycles_started == 1
    assert first.strategy_research.slot(ResearchAgentId.CATALYST_EVENT).state == "waiting_evidence"
    assert restarted.strategy_research.heavy_cycles_started == 0
    assert restarted.strategy_research.slot(ResearchAgentId.INTRADAY_MOMENTUM).evidence_cursor == work.evidence_event_id
    assert first.broker_mutation == first.trading_mutation == 0


def test_combined_tick_ignores_legacy_lockstep_bundle_and_reports_six_slots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: only the obsolete global lockstep bundle path exists.
    config = _config(tmp_path)
    legacy = config.source_paths.outputs_root / "strategy-labs" / "evidence-bundle.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}", encoding="utf-8")
    os.chmod(legacy, 0o600)
    adapter_calls: list[Path] = []

    def legacy_adapter_called(
        _adapter: StrategyLabResultSourceAdapter,
        experiment_ledger: Path,
    ) -> Never:
        adapter_calls.append(experiment_ledger)
        raise AssertionError("production runtime must not call StrategyLabResultSourceAdapter")

    monkeypatch.setattr(StrategyLabResultSourceAdapter, "collect", legacy_adapter_called)

    # When: production OS ticks twice with no independent work queues.
    first = run_research_os_tick(config, POST_CLOSE)
    second = run_research_os_tick(config, POST_CLOSE + dt.timedelta(seconds=30))

    # Then: six independent waiting slots persist and no misleading lockstep success is emitted.
    assert len(first.strategy_research.slots) == 6
    assert {slot.state for slot in first.strategy_research.slots} == {"waiting_evidence"}
    assert second.strategy_research.slots == first.strategy_research.slots
    assert first.daily_reports_projected == 1
    assert second.daily_reports_replayed == 1
    events = HermesDeliveryReader(config.hermes_database).events()
    assert len(events) == 1
    assert events[0].source_event_id == "strategy-research-close-report:2026-08-17"
    assert events[0].rendered_text.count("owner=") == 6
    assert adapter_calls == []


def test_combined_forever_loop_keeps_the_thirty_second_outer_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a production config and a bounded sleep observer.
    config = _config(tmp_path)
    waits: list[float] = []

    class StopLoop(RuntimeError):
        pass

    async def stop_after_first_wait(seconds: float) -> None:
        waits.append(seconds)
        raise StopLoop

    monkeypatch.setattr("trading_agent.research_os_runtime.anyio.sleep", stop_after_first_wait)

    # When: the persistent OS enters its outer loop with default configuration.
    with pytest.raises(StopLoop):
        anyio.run(run_research_os_forever, config)

    # Then: the retained outer cadence is exactly thirty seconds.
    assert waits == [30.0]


def test_tick_cli_subprocess_is_healthy_and_idempotent_across_restart(tmp_path: Path) -> None:
    # Given: a clean combined-runtime config with one independently due work item.
    config = _config(tmp_path).model_copy(update={"hermes_executable": _no_action_hermes(tmp_path / "healthy-hermes")})
    config_path = (tmp_path / "private" / "runtime.json").absolute()
    assert write_research_agent_service_config(config_path, config)
    root = strategy_research_work_root(config)
    root.mkdir(parents=True)
    os.chmod(root, 0o700)
    work = _work(ResearchAgentId.INTRADAY_MOMENTUM, dt.datetime.now(UTC) - dt.timedelta(seconds=1))
    queue_path = root / f"{ResearchAgentId.INTRADAY_MOMENTUM.value}.json"
    queue_path.write_text(StrategyResearchWorkQueue(items=(work,)).model_dump_json(), encoding="utf-8")
    os.chmod(queue_path, 0o600)
    command = (sys.executable, str(Path(run_research_agent_runtime.__file__)), "tick", "--config", str(config_path))

    # When: two separate CLI processes tick the same durable databases.
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    restarted = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: both combined ticks are healthy and strategy work is replay-idempotent.
    first_report = json.loads(first.stdout)
    restarted_report = json.loads(restarted.stdout)
    with ResearchAgentCycleStore(config.cycle_database) as role_store:
        role_results = tuple((item.agent_family_id, item.status, item.reason) for item in role_store.results())
    assert (first.returncode, restarted.returncode) == (0, 0), (
        tuple(first_report["role_agents"].get(key) for key in ("status", "agent_family_id", "result_status")),
        tuple(restarted_report["role_agents"].get(key) for key in ("status", "agent_family_id", "result_status")),
        role_results,
    )
    assert len(restarted_report["strategy_research"]["slots"]) == 6
    assert first_report["strategy_research"]["heavy_cycles_started"] == 1
    assert restarted_report["strategy_research"]["heavy_cycles_started"] == 0
    assert restarted_report["strategy_research"]["slots"][0]["evidence_cursor"] == work.evidence_event_id
    reader = ExperimentLedgerReader(config.source_paths.experiment_ledger)
    assert len(reader.strategy_research_attempts(work.draft.hypothesis_id)) == 1
    assert len(reader.strategy_research_feedback(work.draft.agent_id)) == 1


def test_tick_cli_reports_writer_contention_without_traceback_or_duplicate_work(tmp_path: Path) -> None:
    # Given: a clean config while another process-equivalent writer owns the V9 ledger lease.
    config = _config(tmp_path)
    config_path = (tmp_path / "private" / "runtime.json").absolute()
    assert write_research_agent_service_config(config_path, config)
    command = (sys.executable, str(Path(run_research_agent_runtime.__file__)), "tick", "--config", str(config_path))

    # When: the real CLI tick reaches the contended strategy ledger boundary.
    with ExperimentLedgerStore(config.source_paths.experiment_ledger).writer():
        result = subprocess.run(command, check=False, capture_output=True, text=True)

    # Then: contention is an intentional typed JSON outcome with no partial attempt or traceback.
    assert result.returncode == 3
    assert json.loads(result.stdout) == {
        "broker_mutation": 0,
        "operation": "tick",
        "reason": "experiment_ledger_writer_busy",
        "status": "busy",
        "trading_mutation": 0,
    }
    assert "Traceback" not in result.stderr
    assert ExperimentLedgerReader(config.source_paths.experiment_ledger).strategy_research_preregistrations() == ()
